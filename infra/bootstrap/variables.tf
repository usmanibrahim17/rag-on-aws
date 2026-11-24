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
  description = "GitHub repository (owner/name) whose main branch may assume the deploy role."
  type        = string
  default     = "usmanibrahim17/rag-on-aws"
}

variable "tags" {
  description = "Tags applied to taggable resources."
  type        = map(string)
  default = {
    Project   = "rag-on-aws"
    ManagedBy = "terraform"
  }
}
