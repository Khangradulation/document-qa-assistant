resource "aws_apigatewayv2_api" "http" {
  name          = "${local.prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = local.allowed_origins # explicit origins only, never "*"
    allow_methods = ["GET", "POST", "DELETE", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.http.id
  name             = "cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
    audience = [aws_cognito_user_pool_client.web.id]
  }
}

locals {
  routes = {
    "POST /documents/upload-url"                = "docs"
    "GET /documents"                            = "docs"
    "GET /documents/{document_id}"              = "docs"
    "DELETE /documents/{document_id}"           = "docs"
    "POST /documents/{document_id}/reprocess"   = "docs"
    "GET /documents/{document_id}/download-url" = "docs"
    "POST /query"                               = "query"
  }
}

resource "aws_apigatewayv2_integration" "fn" {
  for_each               = toset(["docs", "query"])
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn[each.key].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = each.key == "query" ? 29000 : 15000
}

resource "aws_apigatewayv2_route" "r" {
  for_each           = local.routes
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.fn[each.value].id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_lambda_permission" "apigw" {
  for_each      = toset(["docs", "query"])
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.prefix}-api"
  retention_in_days = var.log_retention_days
  #checkov:skip=CKV_AWS_158:CloudWatch default encryption is sufficient; access logs hold no document content.
  #checkov:skip=CKV_AWS_338:Retention is configurable (default 30 days) to control cost.
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  # Whole-API throttle: protects the Bedrock bill from abuse or runaway clients.
  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate
    throttling_burst_limit = var.api_throttle_burst
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    # No client IPs, no headers, no bodies.
    format = jsonencode({
      requestId        = "$context.requestId"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      responseLatency  = "$context.responseLatency"
      integrationError = "$context.integrationErrorMessage"
    })
  }
}
