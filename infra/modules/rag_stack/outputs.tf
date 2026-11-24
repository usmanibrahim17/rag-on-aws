output "docs_bucket_name" {
  description = "Name of the S3 bucket holding source documents."
  value       = aws_s3_bucket.docs.bucket
}

output "docs_bucket_arn" {
  description = "ARN of the docs bucket."
  value       = aws_s3_bucket.docs.arn
}

output "vector_bucket_name" {
  description = "Name of the S3 Vectors bucket."
  value       = aws_s3vectors_vector_bucket.this.vector_bucket_name
}

output "vector_bucket_arn" {
  description = "ARN of the S3 Vectors bucket."
  value       = aws_s3vectors_vector_bucket.this.vector_bucket_arn
}

output "vector_index_name" {
  description = "Name of the S3 Vectors index."
  value       = aws_s3vectors_index.chunks.index_name
}

output "vector_index_arn" {
  description = "ARN of the S3 Vectors index."
  value       = aws_s3vectors_index.chunks.index_arn
}

output "ingest_function_name" {
  description = "Name of the ingest Lambda function."
  value       = aws_lambda_function.ingest.function_name
}

output "ingest_queue_url" {
  description = "URL of the SQS queue that buffers S3 events for the ingest Lambda."
  value       = aws_sqs_queue.ingest.url
}

output "ingest_dlq_url" {
  description = "URL of the dead-letter queue for ingest messages that failed 5 times."
  value       = aws_sqs_queue.ingest_dlq.url
}

output "ingest_log_group_name" {
  description = "CloudWatch log group of the ingest Lambda."
  value       = aws_cloudwatch_log_group.ingest.name
}

output "query_function_name" {
  description = "Name of the query Lambda function."
  value       = aws_lambda_function.query.function_name
}

output "query_log_group_name" {
  description = "CloudWatch log group of the query Lambda."
  value       = aws_cloudwatch_log_group.query.name
}

output "api_url" {
  description = "Base URL of the HTTP API (default stage)."
  value       = aws_apigatewayv2_api.ask.api_endpoint
}

output "ask_url" {
  description = "Full URL of POST /ask."
  value       = "${aws_apigatewayv2_api.ask.api_endpoint}/ask"
}

output "budget_name" {
  description = "Name of the monthly AWS Budget."
  value       = aws_budgets_budget.monthly.name
}
