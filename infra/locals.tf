locals {
  prefix     = "${var.app_name}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id

  docs_bucket_name     = "${local.prefix}-docs-${local.account_id}"
  frontend_bucket_name = "${local.prefix}-web-${local.account_id}"
  vector_bucket_name   = "${local.prefix}-vectors-${local.account_id}"
  vector_index_name    = "chunks"
  table_name           = "${local.prefix}-documents"

  frontend_origin = "https://${aws_cloudfront_distribution.frontend.domain_name}"
  allowed_origins = concat([local.frontend_origin], var.extra_allowed_origins)

  embed_model_arn = "arn:aws:bedrock:${var.region}::foundation-model/${var.embed_model_id}"
  generation_model_arns = (
    length(var.generation_model_arns) > 0
    ? var.generation_model_arns
    : ["arn:aws:bedrock:${var.region}::foundation-model/${var.generation_model_id}"]
  )

  vector_index_arn = aws_s3vectors_index.chunks.index_arn

  boundary = var.permissions_boundary_arn == "" ? null : var.permissions_boundary_arn
}
