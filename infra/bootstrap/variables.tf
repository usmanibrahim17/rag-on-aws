variable "name_prefix" {
  description = "Prefix of the stack's resource names; the deploy role may only manage <prefix>-* resources."
  type        = string
  default     = "rag-demo"
}

variable "region" {
  description = "AWS region of the stack and the state bucket."
  type        = string
  default     = "us-east-1"
}

variable "github_repo" {
  description = "GitHub repository (owner/name), for documentation; the trust policy itself uses github_sub_prefix."
  type        = string
  default     = "usmanibrahim17/rag-on-aws"
}

variable "github_sub_prefix" {
  description = <<-EOT
    Prefix of the "sub" claim in GitHub's OIDC token for this repository. This repo uses GitHub's
    immutable subject claims, which embed the numeric owner and repository IDs (stable across
    renames, so a recycled repo name cannot impersonate it). Read it with:
      gh api repos/<owner>/<repo>/actions/oidc/customization/sub --jq .sub_claim_prefix
  EOT
  type        = string
  default     = "repo:usmanibrahim17@226472818/rag-on-aws@1431049385"
}

variable "tags" {
  description = "Tags applied to taggable resources."
  type        = map(string)
  default = {
    Project   = "rag-on-aws"
    ManagedBy = "terraform"
  }
}
