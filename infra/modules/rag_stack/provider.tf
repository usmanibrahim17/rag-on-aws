# AI provider selection (gemini | bedrock). Shared by both Lambdas.

locals {
  use_bedrock = var.ai_provider == "bedrock"
  use_gemini  = var.ai_provider == "gemini"

  # ARN of the Gemini key parameter. It is referenced by name only: Terraform never creates,
  # reads or stores it, so the key cannot end up in code, tfvars or state.
  gemini_key_param_arn = "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/${trimprefix(var.gemini_api_key_parameter_name, "/")}"

  # Environment shared by both Lambdas; the provider-specific names match app/common/config.py.
  # tomap() keeps both conditional branches the same type.
  ai_env_common = {
    AI_PROVIDER         = var.ai_provider
    EMBEDDING_DIMENSION = tostring(var.embedding_dimension)
  }
  ai_env_provider = local.use_gemini ? tomap({
    GEMINI_EMBEDDING_MODEL = var.gemini_embedding_model
    GEMINI_API_KEY_PARAM   = var.gemini_api_key_parameter_name
    }) : tomap({
    EMBEDDING_MODEL_ID = var.embedding_model_id
  })
  ai_env_query_provider = local.use_gemini ? tomap({
    GEMINI_GENERATION_MODEL = var.gemini_generation_model
    }) : tomap({
    GENERATION_MODEL_ID = var.generation_model_id
  })
}

# Plan-time guard: fail with a clear message instead of deploying Lambdas that cannot start.
resource "terraform_data" "check_provider_settings" {
  lifecycle {
    precondition {
      condition     = !local.use_gemini || var.gemini_generation_model != ""
      error_message = "gemini_generation_model is required when ai_provider = \"gemini\"."
    }
    precondition {
      condition     = !local.use_bedrock || var.generation_model_id != ""
      error_message = "generation_model_id is required when ai_provider = \"bedrock\"."
    }
  }
}
