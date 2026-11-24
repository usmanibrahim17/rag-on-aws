terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.24"
    }
  }
}

provider "aws" {
  region = "us-east-1" # widest Bedrock model access
}

module "rag_stack" {
  source = "../../modules/rag_stack"

  name_prefix         = var.name_prefix
  monthly_budget_usd  = var.monthly_budget_usd
  budget_alert_emails = var.budget_alert_emails

  # Built by scripts/build_lambdas.ps1 (or .sh); must exist before plan/validate.
  ingest_zip_path = "${path.root}/../../../dist/ingest.zip"
  query_zip_path  = "${path.root}/../../../dist/query.zip"

  ingest_max_concurrency = var.ingest_max_concurrency

  ai_provider                   = var.ai_provider
  embedding_dimension           = var.embedding_dimension
  gemini_embedding_model        = var.gemini_embedding_model
  gemini_generation_model       = var.gemini_generation_model
  gemini_api_key_parameter_name = var.gemini_api_key_parameter_name
  generation_model_id           = var.generation_model_id # used only when ai_provider = "bedrock"

  # Demo env only: lets `terraform destroy` empty and delete the buckets in one step.
  force_destroy = true

  tags = {
    Project   = "rag-on-aws"
    ManagedBy = "terraform"
  }
}
