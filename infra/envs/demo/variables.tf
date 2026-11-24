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

variable "distance_threshold" {
  description = "Refuse (\"I don't know\") without calling the LLM when the best cosine distance is above this. Tuned from evals/RESULTS.md: answerable questions topped out at 0.357, off-topic ones at 0.47-0.50."
  type        = number
  default     = 0.4
}

variable "ingest_max_concurrency" {
  description = "Max parallel ingest Lambdas (>= 2). Keep low on the Gemini free tier."
  type        = number
  default     = 2
}

variable "ai_provider" {
  description = "AI provider for embeddings and generation: \"gemini\" (now) or \"bedrock\" (when quota allows). Switching requires re-ingesting."
  type        = string
  default     = "gemini"
}

variable "embedding_dimension" {
  description = "Vector index dimension: 768 for Gemini (outputDimensionality), 1024 for Bedrock Titan v2."
  type        = number
  default     = 768
}

variable "gemini_embedding_model" {
  description = "Gemini embedding model."
  type        = string
  default     = "gemini-embedding-001"
}

variable "gemini_generation_model" {
  description = "Gemini generation model (required when ai_provider = gemini)."
  type        = string
  default     = ""
}

variable "gemini_api_key_parameter_name" {
  description = "Existing SSM SecureString holding the Gemini API key (not managed by Terraform)."
  type        = string
  default     = "/rag-demo/gemini-api-key"
}

variable "generation_model_id" {
  description = "Bedrock model or inference profile ID; only used when ai_provider = bedrock."
  type        = string
  default     = ""
}

variable "budget_alert_emails" {
  description = "Email addresses that receive budget alerts."
  type        = list(string)
}
