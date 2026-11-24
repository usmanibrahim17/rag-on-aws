# Permissions boundary for the stack's own roles (the ingest and query Lambda roles).
#
# A boundary is a ceiling: a role's effective permissions are the INTERSECTION of its own
# policies and the boundary. The deploy role can only create or modify <prefix>-* roles that
# carry this boundary (see deploy_iam.tf), so even if the main branch were compromised it could
# not mint a role with more power than this: no IAM, no admin, no data outside this stack.
#
# The list is exactly what the two Lambdas use, for both AI providers.

data "aws_iam_policy_document" "stack_boundary" {
  statement {
    sid       = "ReadDocs"
    actions   = ["s3:GetObject"]
    resources = ["${local.docs_bucket}/docs/*"]
  }

  # Titan / Bedrock models (only used when ai_provider = bedrock).
  statement {
    sid     = "InvokeBedrockModels"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:*:${local.acct}:inference-profile/*",
    ]
  }

  statement {
    sid       = "UseVectorIndex"
    actions   = ["s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors", "s3vectors:QueryVectors"]
    resources = ["${local.vector_bucket}/index/*"]
  }

  # The Gemini API key (only used when ai_provider = gemini).
  statement {
    sid       = "ReadProviderKey"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${local.region}:${local.acct}:parameter/${local.p}/*"]
  }

  statement {
    sid       = "ConsumeIngestQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [local.sqs_arn]
  }

  statement {
    sid       = "WriteLambdaLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.lambda_logs}:*"]
  }
}

resource "aws_iam_policy" "stack_boundary" {
  name        = "${local.p}-stack-boundary"
  description = "Permissions boundary for ${local.p}-* roles: the maximum the stack's Lambdas may ever be granted"
  policy      = data.aws_iam_policy_document.stack_boundary.json
  tags        = var.tags
}
