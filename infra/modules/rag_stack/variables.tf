variable "name_prefix" {
  description = "Prefix for resource names, e.g. \"rag-demo\"."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD; alerts fire at 40/80/100% actual and 100% forecasted."
  type        = number
  default     = 20
}

variable "budget_alert_emails" {
  description = "Email addresses that receive budget alerts."
  type        = list(string)

  validation {
    condition     = length(var.budget_alert_emails) > 0
    error_message = "Provide at least one email address for budget alerts."
  }
}

variable "embedding_dimension" {
  description = "Vector dimension; must match the embedding model (Titan Text v2 = 1024)."
  type        = number
  default     = 1024
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete the docs and vector buckets even when they contain data."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to all taggable resources."
  type        = map(string)
  default     = {}
}
