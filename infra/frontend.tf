# Static React app: PRIVATE S3 bucket served through CloudFront (HTTPS) using
# Origin Access Control. The bucket itself is never public, and it is a
# different bucket from the one holding PDFs.
resource "aws_s3_bucket" "frontend" {
  bucket        = local.frontend_bucket_name
  force_destroy = var.force_destroy
  #checkov:skip=CKV_AWS_18:Access logging omitted to save cost.
  #checkov:skip=CKV_AWS_144:Replication not needed for static assets.
  #checkov:skip=CKV2_AWS_62:No event notifications needed.
  #checkov:skip=CKV_AWS_145:SSE-S3 is sufficient for public static assets.
  #checkov:skip=CKV2_AWS_61:Assets are overwritten on each deploy; a lifecycle rule adds nothing.
  #checkov:skip=CKV_AWS_21:Versioning not needed; the site is rebuilt from Git.
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.prefix}-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # cheapest edge locations
  comment             = "${local.prefix} frontend"
  #checkov:skip=CKV_AWS_86:Access logging omitted to save cost.
  #checkov:skip=CKV_AWS_310:Single origin; no failover needed for a demo.
  #checkov:skip=CKV_AWS_374:Geo restriction not required.
  #checkov:skip=CKV2_AWS_32:A managed security-headers response policy IS attached.
  #checkov:skip=CKV2_AWS_47:WAF costs ~$5+/month; out of scope for the demo (documented).
  #checkov:skip=CKV_AWS_68:WAF costs ~$5+/month; out of scope for the demo (documented).
  #checkov:skip=CKV_AWS_305:default_root_object is set.
  #checkov:skip=CKV_AWS_174:The free *.cloudfront.net certificate cannot enforce a TLS minimum (AWS limitation). A custom domain + ACM cert is needed; documented as a known limitation.
  #checkov:skip=CKV2_AWS_42:Custom domain/certificate is out of scope for the demo.

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "frontend-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    target_origin_id           = "frontend-s3"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
  }

  # Single-page app: unknown paths fall back to index.html
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }
}

data "aws_iam_policy_document" "frontend_bucket" {
  statement {
    sid       = "AllowCloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.frontend.arn]
    }
  }
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.frontend.arn, "${aws_s3_bucket.frontend.arn}/*"]
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

resource "aws_s3_bucket_policy" "frontend" {
  bucket     = aws_s3_bucket.frontend.id
  policy     = data.aws_iam_policy_document.frontend_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.frontend]
}
