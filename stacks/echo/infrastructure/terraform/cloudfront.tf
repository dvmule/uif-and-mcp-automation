# ui.tf
# Static React UI hosting: private S3 bucket served via CloudFront with OAC.
# SPA routing: 403/404 errors redirected to index.html.



# ── S3 bucket ─────────────────────────────────────────────────────

resource "aws_s3_bucket" "ui" {
  bucket = local.ui_bucket

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_public_access_block" "ui" {
  bucket                  = aws_s3_bucket.ui.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── CloudFront Origin Access Control ─────────────────────────────

resource "aws_cloudfront_origin_access_control" "ui" {
  name                              = "${local.name_prefix}-ui-oac"
  description                       = "OAC for the EDS UI static site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ── CloudFront distribution ───────────────────────────────────────

resource "aws_cloudfront_distribution" "ui" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = var.cloudfront_price_class
  # UIFPE-355: a CloudFront distribution has no name attribute, so the standard
  # puts the name in the comment field. Also clears the stale "EDS" branding.
  comment = "${local.name_prefix}-ui"

  aliases = var.ui_domain != "" ? [var.ui_domain] : []

  # `one()` rather than a ternary on local.ui_waf_enabled: a conditional does
  # not reliably short-circuit, so `aws_wafv2_web_acl.ui[0].arn` in the true
  # branch can still be evaluated when count is 0 and fail on the index.
  # one() takes the splat -- the ARN when the ACL exists, null when it does
  # not, which is the same value CloudFront gets today.
  #
  # Null here is also why trivy's AWS-0011 ("no WAF in front of the
  # distribution") stays OPEN against this resource while var.enable_ui_waf is
  # false. That finding tracks the deployed posture honestly; suppressing it
  # because the code exists would claim protection nothing is enforcing.
  web_acl_id = one(aws_wafv2_web_acl.ui[*].arn)

  origin {
    domain_name              = aws_s3_bucket.ui.bucket_regional_domain_name
    origin_id                = "s3-ui-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.ui.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-ui-origin"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 3600
    max_ttl     = 86400
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.ui_domain == ""
  }

  tags = {
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── S3 bucket policy: allow CloudFront OAC only ───────────────────

data "aws_iam_policy_document" "ui_bucket_policy" {
  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.ui.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.ui.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "ui" {
  bucket = aws_s3_bucket.ui.id
  policy = data.aws_iam_policy_document.ui_bucket_policy.json

  depends_on = [aws_s3_bucket_public_access_block.ui]
}

# ── Outputs ───────────────────────────────────────────────────────



