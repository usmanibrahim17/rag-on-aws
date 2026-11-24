# Ingest Lambda: S3 ObjectCreated/ObjectRemoved under docs/ -> SQS -> chunk -> embed -> S3 Vectors.

locals {
  ingest_function_name   = "${var.name_prefix}-ingest"
  ingest_timeout_seconds = 300 # the queue's visibility timeout is derived from this (queue.tf)
}

# Created explicitly (before the function) so retention applies; otherwise Lambda
# auto-creates an unmanaged log group that never expires.
resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.ingest_function_name}"
  retention_in_days = 14
  tags              = var.tags
}

# No reserved concurrency on purpose: new accounts have low concurrency quotas.
resource "aws_lambda_function" "ingest" {
  function_name = local.ingest_function_name
  role          = aws_iam_role.ingest.arn
  runtime       = "python3.12"
  handler       = "app.ingest.handler.handler"
  memory_size   = 512
  timeout       = local.ingest_timeout_seconds

  filename = var.ingest_zip_path
  # Changes whenever the zip's content changes, so new code redeploys.
  source_code_hash = filebase64sha256(var.ingest_zip_path)

  # Names must match app/common/config.py.
  environment {
    variables = merge(
      local.ai_env_common,
      local.ai_env_provider,
      {
        VECTOR_BUCKET_NAME = aws_s3vectors_vector_bucket.this.vector_bucket_name
        VECTOR_INDEX_NAME  = aws_s3vectors_index.chunks.index_name
        CHUNK_SIZE         = tostring(var.chunk_size)
        CHUNK_OVERLAP      = tostring(var.chunk_overlap)
      },
    )
  }

  tags = var.tags

  depends_on = [
    terraform_data.check_provider_settings,
    aws_cloudwatch_log_group.ingest,
    aws_iam_role_policy.ingest,
  ]
}

# The S3 trigger, queue and event source mapping live in queue.tf.
