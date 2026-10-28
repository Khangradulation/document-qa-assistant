locals {
  functions = {
    docs = {
      name    = "${local.prefix}-docs-api"
      handler = "docqa.handlers.docs_api.lambda_handler"
      timeout = 15
      memory  = 256
    }
    query = {
      name    = "${local.prefix}-query-api"
      handler = "docqa.handlers.query_api.lambda_handler"
      timeout = 28 # API Gateway HTTP APIs time out at 30s
      memory  = 512
    }
    ingest = {
      name    = "${local.prefix}-ingest"
      handler = "docqa.handlers.ingest_handler.lambda_handler"
      timeout = 300
      memory  = 1024
    }
  }

  common_env = {
    DOCS_BUCKET         = aws_s3_bucket.docs.bucket
    VECTOR_BUCKET       = aws_s3vectors_vector_bucket.vectors.vector_bucket_name
    VECTOR_INDEX        = aws_s3vectors_index.chunks.index_name
    TABLE_NAME          = aws_dynamodb_table.documents.name
    EMBED_MODEL_ID      = var.embed_model_id
    EMBED_DIMENSIONS    = tostring(var.embed_dimensions)
    GENERATION_MODEL_ID = var.generation_model_id
    TOP_K               = tostring(var.top_k)
    MAX_DISTANCE        = tostring(var.max_distance)
  }

  package_hash        = filebase64sha256("${path.module}/${var.lambda_package}")
  ingest_function_arn = "arn:aws:lambda:${var.region}:${local.account_id}:function:${local.prefix}-ingest" # built from the name to avoid a dependency cycle
}

# ---- Log groups (explicit retention; created before the functions) ---------
resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${each.value.name}"
  retention_in_days = var.log_retention_days
  #checkov:skip=CKV_AWS_158:CloudWatch default encryption is sufficient; logs contain no document text.
  #checkov:skip=CKV_AWS_338:Retention is configurable (default 30 days) to control cost.
}

# ---- Assume-role policy shared by all three --------------------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

locals {
  docs_objects = "${aws_s3_bucket.docs.arn}/uploads/*"
  table_arn    = aws_dynamodb_table.documents.arn
  log_arn      = { for k, lg in aws_cloudwatch_log_group.fn : k => "${lg.arn}:*" }
}

# ===== Role 1: docs API (manage documents) ==================================
resource "aws_iam_role" "docs" {
  name                 = "${local.prefix}-docs-api"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = local.boundary
}

data "aws_iam_policy_document" "docs" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.log_arn["docs"]]
  }
  statement {
    sid       = "SignAndDeleteObjects" # presigned URLs carry THIS role's permissions
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = [local.docs_objects]
  }
  statement {
    sid       = "DocumentRecords"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query"]
    resources = [local.table_arn]
  }
  statement {
    sid       = "DeleteVectors"
    actions   = ["s3vectors:DeleteVectors"]
    resources = [local.vector_index_arn]
  }
  statement {
    sid       = "TriggerReprocess"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.ingest_function_arn]
  }
}

resource "aws_iam_role_policy" "docs" {
  name   = "docs-api"
  role   = aws_iam_role.docs.id
  policy = data.aws_iam_policy_document.docs.json
}

# ===== Role 2: query API (read-only data plane + model) =====================
resource "aws_iam_role" "query" {
  name                 = "${local.prefix}-query-api"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = local.boundary
}

data "aws_iam_policy_document" "query" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.log_arn["query"]]
  }
  statement {
    sid       = "ReadRecords"
    actions   = ["dynamodb:GetItem"]
    resources = [local.table_arn]
  }
  statement {
    sid       = "SearchVectors" # GetVectors is required when filtering / returning metadata
    actions   = ["s3vectors:QueryVectors", "s3vectors:GetVectors"]
    resources = [local.vector_index_arn]
  }
  statement {
    sid       = "InvokeModels"
    actions   = ["bedrock:InvokeModel"]
    resources = concat([local.embed_model_arn], local.generation_model_arns)
  }
}

resource "aws_iam_role_policy" "query" {
  name   = "query-api"
  role   = aws_iam_role.query.id
  policy = data.aws_iam_policy_document.query.json
}

# ===== Role 3: ingestion ====================================================
resource "aws_iam_role" "ingest" {
  name                 = "${local.prefix}-ingest"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = local.boundary
}

data "aws_iam_policy_document" "ingest" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.log_arn["ingest"]]
  }
  statement {
    sid       = "ReadAndRejectUploads"
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = [local.docs_objects]
  }
  statement {
    sid       = "UpdateRecords"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [local.table_arn]
  }
  statement {
    sid       = "WriteVectors"
    actions   = ["s3vectors:PutVectors", "s3vectors:DeleteVectors"]
    resources = [local.vector_index_arn]
  }
  statement {
    sid       = "EmbedOnly" # ingestion never needs the text-generation model
    actions   = ["bedrock:InvokeModel"]
    resources = [local.embed_model_arn]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

# ---- Functions --------------------------------------------------------------
locals {
  role_arns = {
    docs   = aws_iam_role.docs.arn
    query  = aws_iam_role.query.arn
    ingest = aws_iam_role.ingest.arn
  }
}

resource "aws_lambda_function" "fn" {
  for_each = local.functions

  function_name = each.value.name
  role          = local.role_arns[each.key]
  runtime       = "python3.14"
  architectures = ["arm64"] # cheaper; all our dependencies are pure Python
  handler       = each.value.handler
  timeout       = each.value.timeout
  memory_size   = each.value.memory

  filename         = "${path.module}/${var.lambda_package}"
  source_code_hash = local.package_hash

  reserved_concurrent_executions = each.key == "ingest" ? var.ingest_reserved_concurrency : -1

  environment {
    variables = merge(
      local.common_env,
      each.key == "docs" ? { INGEST_FUNCTION_NAME = "${local.prefix}-ingest" } : {}
    )
  }

  depends_on = [aws_cloudwatch_log_group.fn]

  #checkov:skip=CKV_AWS_117:Not in a VPC on purpose: a VPC would need NAT gateways (cost) to reach Bedrock/S3.
  #checkov:skip=CKV_AWS_116:Async retries are handled by Lambda's default retry policy plus alarms; no DLQ in this demo.
  #checkov:skip=CKV_AWS_173:Environment variables hold only resource names, no secrets.
  #checkov:skip=CKV_AWS_272:Code signing is out of scope for a demo.
  #checkov:skip=CKV_AWS_50:X-Ray tracing omitted to save cost.
}

# Async-invocation behaviour for ingestion: 2 retries, give up after 1 hour.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name                = aws_lambda_function.fn["ingest"].function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

# ---- S3 -> ingest trigger ---------------------------------------------------
resource "aws_lambda_permission" "s3_invoke_ingest" {
  statement_id   = "AllowS3Invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.fn["ingest"].function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.docs.arn
  source_account = local.account_id
}

resource "aws_s3_bucket_notification" "docs" {
  bucket = aws_s3_bucket.docs.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.fn["ingest"].arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
    filter_suffix       = ".pdf"
  }

  depends_on = [aws_lambda_permission.s3_invoke_ingest]
}
