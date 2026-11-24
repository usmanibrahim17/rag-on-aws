# IAM role for the ingest Lambda. Rule: each role gets only the actions it uses,
# scoped to specific ARNs. The query Lambda's role is added in M3.

data "aws_region" "current" {}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${var.name_prefix}-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  # Ceiling on what this role can ever do, whatever its policies say (see infra/bootstrap).
  permissions_boundary = var.permissions_boundary_arn
  tags                 = var.tags
}

data "aws_iam_policy_document" "ingest" {
  # Read source documents (only under docs/).
  statement {
    sid       = "ReadDocs"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/docs/*"]
  }

  # Bedrock provider only: embed chunks with exactly one foundation model.
  dynamic "statement" {
    for_each = local.use_bedrock ? [1] : []

    content {
      sid       = "EmbedWithTitan"
      actions   = ["bedrock:InvokeModel"]
      resources = ["arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/${var.embedding_model_id}"]
    }
  }

  # Gemini provider only: read the API key (one parameter). No kms:Decrypt statement: for a
  # SecureString under the default aws/ssm key, AWS documents that all principals in the
  # account may use the key, so ssm:GetParameter is enough. Verify on the first real call.
  dynamic "statement" {
    for_each = local.use_gemini ? [1] : []

    content {
      sid       = "ReadGeminiKey"
      actions   = ["ssm:GetParameter"]
      resources = [local.gemini_key_param_arn]
    }
  }

  # Poll the ingest queue (the Lambda event source mapping needs exactly these three).
  statement {
    sid       = "ReadIngestQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.ingest.arn]
  }

  # Write/probe/delete vectors in our one index. The code never lists, so no ListVectors.
  statement {
    sid       = "WriteVectors"
    actions   = ["s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors"]
    resources = [aws_s3vectors_index.chunks.index_arn]
  }

  # Logs go only to the Lambda's own (Terraform-managed) log group; ":*" = its log streams.
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}
