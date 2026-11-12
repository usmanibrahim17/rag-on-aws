# Query Lambda + HTTP API: POST /ask -> embed question -> retrieve -> Bedrock Converse.

locals {
  query_function_name = "${var.name_prefix}-query"

  # Cross-region inference profile IDs start with a geography prefix ("us.", "eu.", ...).
  # Only relevant when ai_provider = bedrock.
  generation_is_profile = local.use_bedrock && can(regex("^(us|eu|apac|global|us-gov|jp|au|ca)\\.", var.generation_model_id))
}

# ---------------------------------------------------------------------------
# Which resources may the Lambda invoke for generation?
#
# Plain model ID  -> InvokeModel on that foundation model in this region.
# Inference profile -> Bedrock authorizes the call against BOTH the profile ARN and the
#   foundation-model ARN in whichever region it routes the request to. The profile can route
#   to several regions, so we look the profile up and grant every underlying model ARN it lists.
#   (Needs bedrock:GetInferenceProfile for whoever runs terraform plan/apply.)
# ---------------------------------------------------------------------------
data "aws_bedrock_inference_profile" "generation" {
  count                = local.generation_is_profile ? 1 : 0
  inference_profile_id = var.generation_model_id
}

locals {
  generation_resource_arns = local.generation_is_profile ? concat(
    [data.aws_bedrock_inference_profile.generation[0].inference_profile_arn],
    [for m in data.aws_bedrock_inference_profile.generation[0].models : m.model_arn],
    ) : [
    "arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/${var.generation_model_id}",
  ]
}

# ---------------------------------------------------------------------------
# Logging + IAM
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "query" {
  name              = "/aws/lambda/${local.query_function_name}"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_iam_role" "query" {
  name               = "${var.name_prefix}-query"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "query" {
  # Bedrock provider only: embed the question (same Titan model as ingest).
  dynamic "statement" {
    for_each = local.use_bedrock ? [1] : []

    content {
      sid       = "EmbedWithTitan"
      actions   = ["bedrock:InvokeModel"]
      resources = ["arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/${var.embedding_model_id}"]
    }
  }

  # Bedrock provider only: generate the answer. Converse is authorized by InvokeModel.
  dynamic "statement" {
    for_each = local.use_bedrock ? [1] : []

    content {
      sid       = "GenerateAnswer"
      actions   = ["bedrock:InvokeModel"]
      resources = local.generation_resource_arns
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

  # QueryVectors does the search. returnMetadata=true additionally requires GetVectors
  # (the S3 Vectors docs say the request fails with 403 without it).
  statement {
    sid       = "QueryIndex"
    actions   = ["s3vectors:QueryVectors", "s3vectors:GetVectors"]
    resources = [aws_s3vectors_index.chunks.index_arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.query.arn}:*"]
  }
}

resource "aws_iam_role_policy" "query" {
  name   = "query"
  role   = aws_iam_role.query.id
  policy = data.aws_iam_policy_document.query.json
}

# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "query" {
  function_name = local.query_function_name
  role          = aws_iam_role.query.arn
  runtime       = "python3.12"
  handler       = "app.query.handler.handler"
  memory_size   = 512
  timeout       = 30

  filename         = var.query_zip_path
  source_code_hash = filebase64sha256(var.query_zip_path)

  # Names must match app/common/config.py (load_query_settings).
  environment {
    variables = merge(
      local.ai_env_common,
      local.ai_env_provider,
      local.ai_env_query_provider,
      {
        VECTOR_BUCKET_NAME = aws_s3vectors_vector_bucket.this.vector_bucket_name
        VECTOR_INDEX_NAME  = aws_s3vectors_index.chunks.index_name
        TOP_K              = tostring(var.top_k)
        DISTANCE_THRESHOLD = tostring(var.distance_threshold)
        MAX_OUTPUT_TOKENS  = tostring(var.max_output_tokens)
      },
    )
  }

  tags = var.tags

  depends_on = [
    terraform_data.check_provider_settings,
    aws_cloudwatch_log_group.query,
    aws_iam_role_policy.query,
  ]
}

# ---------------------------------------------------------------------------
# HTTP API (v2): cheaper and simpler than REST APIs; payload format 2.0.
# ---------------------------------------------------------------------------
resource "aws_apigatewayv2_api" "ask" {
  name          = "${var.name_prefix}-api"
  protocol_type = "HTTP"
  tags          = var.tags

  # Open to any origin for now; restricted to the CloudFront domain in M5.
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "query" {
  api_id                 = aws_apigatewayv2_api.ask.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.query.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000 # matches the Lambda timeout (API Gateway's maximum)
}

resource "aws_apigatewayv2_route" "ask" {
  api_id    = aws_apigatewayv2_api.ask.id
  route_key = "POST /ask"
  target    = "integrations/${aws_apigatewayv2_integration.query.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.ask.id
  name        = "$default"
  auto_deploy = true
  tags        = var.tags

  # Throttling protects the Bedrock bill from abuse of the public endpoint.
  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate_limit
    throttling_burst_limit = var.api_throttle_burst_limit
  }
}

# Lets API Gateway (this API only) invoke the function.
resource "aws_lambda_permission" "apigw_invoke_query" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ask.execution_arn}/*/*"
}
