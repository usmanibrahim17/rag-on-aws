# Ingest Lambda: S3 ObjectCreated/ObjectRemoved under docs/ -> chunk -> embed -> S3 Vectors.

locals {
  ingest_function_name = "${var.name_prefix}-ingest"
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
  timeout       = 300

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

# Lets S3 (this bucket, this account only) invoke the function.
resource "aws_lambda_permission" "s3_invoke_ingest" {
  statement_id   = "AllowS3Invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.ingest.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.docs.arn
  source_account = data.aws_caller_identity.current.account_id
}

# One filter per suffix (S3 filters can't OR suffixes). ObjectRemoved:* also covers
# DeleteMarkerCreated, which is what a normal delete on a versioned bucket emits.
resource "aws_s3_bucket_notification" "docs" {
  bucket = aws_s3_bucket.docs.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest.arn
    events              = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
    filter_prefix       = "docs/"
    filter_suffix       = ".md"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest.arn
    events              = ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
    filter_prefix       = "docs/"
    filter_suffix       = ".txt"
  }

  # S3 validates the destination when saving the notification, so the permission must exist first.
  depends_on = [aws_lambda_permission.s3_invoke_ingest]
}
