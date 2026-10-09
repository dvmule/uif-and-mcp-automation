# UI: private S3 bucket served through CloudFront with Origin Access Control.
# /api/* routes are NOT here — the UI calls same-origin /api which CloudFront
# forwards to the API Gateway origin (added below as a second origin), so the
# deployed shape matches the local dev proxy exactly.

resource "aws_cloudfront_origin_access_control" "ui" {
  name                              = "${local.name}-ui"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "ui" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "${local.name} control panel UI"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.ui.bucket_regional_domain_name
    origin_id                = "ui-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.ui.id
  }

  # API origin: this stack's own API Gateway HTTP API (Lambda, UIFPE-226).
  # The ALB alternative was deleted at probation exit (2026-08-28); restoring
  # it is a git revert, not a tfvars flip. api_gateway_domain still overrides
  # (a platform gateway interposed in front).
  origin {
    domain_name = (var.api_gateway_domain != "" ? var.api_gateway_domain :
    replace(aws_apigatewayv2_api.api.api_endpoint, "https://", ""))
    origin_id = "api-gw"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "ui-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  ordered_cache_behavior {
    path_pattern           = "/api/*"
    target_origin_id       = "api-gw"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.strip_api_prefix.arn
    }
    forwarded_values {
      query_string = true
      headers      = ["Authorization", "Content-Type"]
      cookies { forward = "none" }
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  # SPA routing: unknown paths return index.html
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # custom domain + ACM cert per env later
  }
}

# The UI calls same-origin /api/*; the API serves routes without the prefix
# (locally the Vite dev proxy strips it — this function is the deployed
# equivalent, keeping one API surface everywhere).
resource "aws_cloudfront_function" "strip_api_prefix" {
  name    = "${local.name}-strip-api-prefix"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      request.uri = request.uri.replace(/^\/api/, "");
      if (request.uri === "") { request.uri = "/"; }
      return request;
    }
  EOT
}

variable "api_gateway_domain" {
  description = "Override for the CloudFront API origin (e.g. a platform API Gateway in front of an internal ALB). Empty = this stack's own API Gateway (UIFPE-226)."
  type        = string
  default     = ""
}


data "aws_iam_policy_document" "ui_bucket" {
  statement {
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
  policy = data.aws_iam_policy_document.ui_bucket.json
}
