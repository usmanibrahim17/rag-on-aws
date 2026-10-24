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

  # Demo env only: lets `terraform destroy` empty and delete the buckets in one step.
  force_destroy = true

  tags = {
    Project   = "rag-on-aws"
    ManagedBy = "terraform"
  }
}
