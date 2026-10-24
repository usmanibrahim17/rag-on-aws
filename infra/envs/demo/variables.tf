variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "rag-demo"
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD."
  type        = number
  default     = 20
}

variable "budget_alert_emails" {
  description = "Email addresses that receive budget alerts."
  type        = list(string)
}
