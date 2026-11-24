terraform {
  required_version = ">= 1.10" # S3 native state locking (use_lockfile) needs 1.10+

  # Remote state in the bucket created by infra/bootstrap. The bucket name contains the AWS
  # account ID, so it is not committed: pass it at init time (see backend.hcl.example):
  #   terraform init -backend-config=backend.hcl
  # use_lockfile = locking via a ".tflock" object in the same bucket (no DynamoDB table).
  backend "s3" {
    key          = "demo/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }

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

data "aws_caller_identity" "current" {}

module "rag_stack" {
  source = "../../modules/rag_stack"

  # Created by infra/bootstrap. Both Lambda roles carry it, and the CD deploy role may only
  # create or change stack roles that do.
  permissions_boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.name_prefix}-stack-boundary"

  name_prefix         = var.name_prefix
  monthly_budget_usd  = var.monthly_budget_usd
  budget_alert_emails = var.budget_alert_emails

  # Built by scripts/build_lambdas.ps1 (or .sh); must exist before plan/validate.
  ingest_zip_path = "${path.root}/../../../dist/ingest.zip"
  query_zip_path  = "${path.root}/../../../dist/query.zip"

  web_dir              = "${path.root}/../../../web"
  cors_allowed_origins = var.cors_allowed_origins

  ingest_max_concurrency = var.ingest_max_concurrency
  distance_threshold     = var.distance_threshold

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
