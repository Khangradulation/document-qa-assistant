# Nothing secret here: names, ARNs and public URLs only.
output "api_url" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "frontend_url" {
  value = local.frontend_origin
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.frontend.id
}

output "docs_bucket" {
  value = aws_s3_bucket.docs.bucket
}

output "vector_bucket" {
  value = aws_s3vectors_vector_bucket.vectors.vector_bucket_name
}

output "vector_index" {
  value = aws_s3vectors_index.chunks.index_name
}

output "table_name" {
  value = aws_dynamodb_table.documents.name
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.users.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "region" {
  value = var.region
}

output "lambda_functions" {
  value = { for k, f in aws_lambda_function.fn : k => f.function_name }
}
