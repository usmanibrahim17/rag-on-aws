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

output "budget_name" {
  description = "Name of the monthly AWS Budget."
  value       = aws_budgets_budget.monthly.name
}
