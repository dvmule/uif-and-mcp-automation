# UI delivery — private S3 bucket behind CloudFront with origin access control.
#
# Folded in from the pre-migration `s3-frontend` root module (UIFPE-36), which
# was independent of the others and needed no state coupling changes.
#
# The bundle no longer carries the API origin: it is written to config.json at
# publish time and fetched by the browser at boot, so one bundle is valid in
# every environment. CloudFront never proxies the API — the browser calls API
# Gateway directly, which is why the authorizer's IP-allowlist path is what
# credentials the UI.

resource "aws_s3_bucket" "frontend" {
  # uif-{stack}-{env}-{purpose}, same as every other name in this stack.
  #
  # An earlier commit on this branch put the account id first
  # (283556840805-s3-uif-apicurio-dev-ui) on the authority of 00-core.md,
  # which did carry that exception at the time. It no longer does: the
  # account-id-first form was retired from the standard, and the installed
  # naming guard now refuses it by name. 24 characters at the longest env,
  # well inside the 63-char limit, and globally unique because the stack
  # token is.
  bucket        = "${local.name}-ui"
  force_destroy = var.frontend_force_destroy

  tags = { component = "ui" }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration {
    status = "Enabled"
  }
}

# This bucket holds only the compiled public UI bundle. SSE-S3 is
# sufficient; a CMK would add a KMS key policy for the CloudFront OAC and a rotation
# obligation in exchange for confidentiality over bytes served to every browser.
# trivy:ignore:AVD-AWS-0132 See the comment above.
resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.name}-ui"
  description                       = "OAC for the Apicurio governance UI"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# WAFv2 web ACL (waf.tf) restricts this distribution to var.api_allowed_cidrs --
# the same source ranges the API Gateway authorizer already requires. Closed
# 2026-09-15; DEPLOY-DEV.md's "One decision I am not making for you" recorded
# this as accepted-for-dev-only up to that point.
resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = var.frontend_price_class
  comment             = "${local.name}-ui"

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6" # managed CachingOptimized
    response_headers_policy_id = "60669652-455b-4ae9-85a4-c4c02393f86c" # managed CORS-S3Origin
  }

  # index.html is never cached, so a deploy is visible immediately.
  ordered_cache_behavior {
    path_pattern           = "/index.html"
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # managed CachingDisabled
  }

  # SPA routing — unknown paths return index.html so React Router handles them.
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
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  web_acl_id = aws_wafv2_web_acl.ui.arn

  tags = { component = "ui" }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn
        }
      }
    }]
  })
}
