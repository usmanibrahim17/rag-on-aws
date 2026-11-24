# Demo website: private S3 bucket served through CloudFront (Origin Access Control, HTTPS only).
#
# The bucket is never public: only this CloudFront distribution may read it (bucket policy
# conditioned on the distribution ARN). Files are uploaded from var.web_dir by Terraform.

locals {
  web_bucket_name = "${var.name_prefix}-web-${data.aws_caller_identity.current.account_id}"

  # Text files only (read with file()); an unknown extension fails the plan on purpose.
  web_content_types = {
    html = "text/html; charset=utf-8"
    js   = "application/javascript; charset=utf-8"
    css  = "text/css; charset=utf-8"
    svg  = "image/svg+xml"
    json = "application/json"
    txt  = "text/plain; charset=utf-8"
  }
  web_files = toset([for f in fileset(var.web_dir, "*") : f if !startswith(f, ".")]) # skip .gitkeep etc.

  # Where the page may send requests. The wildcard on the API host (not this API's exact URL)
  # avoids a dependency cycle: the API's CORS setting needs the CloudFront domain.
  web_csp = join("; ", [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'", # the chat widget injects a <style> into its Shadow DOM
    "img-src 'self' data:",
    "connect-src 'self' https://*.execute-api.${data.aws_region.current.region}.amazonaws.com",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ])
}

resource "aws_s3_bucket" "web" {
  bucket        = local.web_bucket_name
  force_destroy = var.force_destroy
  tags          = var.tags
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Every file in web/. __API_URL__ in the files is replaced with this stack's /ask URL, so the
# same sources work for any deployment. The etag makes Terraform re-upload when content changes.
resource "aws_s3_object" "web" {
  for_each = local.web_files

  bucket       = aws_s3_bucket.web.id
  key          = each.value
  content      = replace(file("${var.web_dir}/${each.value}"), "__API_URL__", "${aws_apigatewayv2_api.ask.api_endpoint}/ask")
  content_type = local.web_content_types[reverse(split(".", each.value))[0]]
  etag         = md5(replace(file("${var.web_dir}/${each.value}"), "__API_URL__", "${aws_apigatewayv2_api.ask.api_endpoint}/ask"))
  tags         = var.tags
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.name_prefix}-web"
  description                       = "CloudFront signs its requests to the private web bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Short TTL (5 min default) so a deploy shows up quickly without needing invalidations.
resource "aws_cloudfront_cache_policy" "web" {
  name        = "${var.name_prefix}-web"
  min_ttl     = 0
  default_ttl = 300
  max_ttl     = 3600

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true

    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_response_headers_policy" "web" {
  name = "${var.name_prefix}-web-security"

  security_headers_config {
    content_security_policy {
      content_security_policy = local.web_csp
      override                = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
  }
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  is_ipv6_enabled     = true
  http_version        = "http2and3"
  price_class         = "PriceClass_100" # US/Canada/Europe edges: cheapest
  default_root_object = "index.html"
  comment             = "${var.name_prefix} demo site"

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web-bucket"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id           = "web-bucket"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.web.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.web.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = var.tags
}

data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid       = "AllowCloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.web.arn, "${aws_s3_bucket.web.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.web]
}
