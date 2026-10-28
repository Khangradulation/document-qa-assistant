# ---------------------------------------------------------------------------
# Source-document bucket: PRIVATE. Browsers reach it only through short-lived
# presigned URLs that our Lambda signs for one exact object key.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "docs" {
  bucket        = local.docs_bucket_name
  force_destroy = var.force_destroy
  #checkov:skip=CKV_AWS_18:Access logging omitted to save cost in this demo; CloudTrail data events are the production answer.
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for a demo.
  #checkov:skip=CKV2_AWS_62:Event notifications ARE configured (to the ingest Lambda).
  #checkov:skip=CKV_AWS_145:SSE-S3 (AES256) is used instead of a customer KMS key to avoid KMS cost/complexity.
  #checkov:skip=CKV2_AWS_61:Lifecycle rules exist (see aws_s3_bucket_lifecycle_configuration.docs).
  #checkov:skip=CKV_AWS_21:Versioning is OFF on purpose: with it, "delete my document" would leave old copies behind.
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Retention policy: PDFs are kept until the user deletes them (the API deletes the
# object, vectors and record together). Only abandoned multipart uploads expire.
resource "aws_s3_bucket_lifecycle_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

resource "aws_s3_bucket_cors_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  cors_rule {
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = local.allowed_origins
    allowed_headers = ["content-type"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

data "aws_iam_policy_document" "docs_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.docs.arn, "${aws_s3_bucket.docs.arn}/*"]
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

resource "aws_s3_bucket_policy" "docs" {
  bucket     = aws_s3_bucket.docs.id
  policy     = data.aws_iam_policy_document.docs_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.docs]
}

# ---------------------------------------------------------------------------
# Vector bucket + index (Amazon S3 Vectors)
# ---------------------------------------------------------------------------
resource "aws_s3vectors_vector_bucket" "vectors" {
  vector_bucket_name = local.vector_bucket_name
  force_destroy      = var.force_destroy
}

resource "aws_s3vectors_index" "chunks" {
  vector_bucket_name = aws_s3vectors_vector_bucket.vectors.vector_bucket_name
  index_name         = local.vector_index_name
  data_type          = "float32"
  dimension          = var.embed_dimensions
  distance_metric    = "cosine"

  # Chunk text is several KB: too big for filterable metadata, so it is declared
  # non-filterable (still returned with query results). All other keys
  # (document_id, owner_id, ...) stay filterable.
  metadata_configuration {
    non_filterable_metadata_keys = ["text"]
  }
}

# ---------------------------------------------------------------------------
# Document records
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "documents" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST" # no idle cost
  hash_key     = "owner_id"
  range_key    = "document_id"

  attribute {
    name = "owner_id"
    type = "S"
  }
  attribute {
    name = "document_id"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  #checkov:skip=CKV_AWS_119:Uses the AWS-managed DynamoDB key (free); a customer KMS key adds cost.
}
