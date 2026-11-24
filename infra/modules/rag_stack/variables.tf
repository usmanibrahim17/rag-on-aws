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

variable "ai_provider" {
  description = "Which AI provider the Lambdas use: \"gemini\" or \"bedrock\". Changing it changes the embedding space, so recreate the index and re-ingest."
  type        = string
  default     = "bedrock"

  validation {
    condition     = contains(["gemini", "bedrock"], var.ai_provider)
    error_message = "ai_provider must be \"gemini\" or \"bedrock\"."
  }
}

variable "embedding_dimension" {
  description = "Vector dimension of the index; must match the embedding model (Titan v2 = 1024, Gemini embedding with outputDimensionality 768 = 768). Changing it replaces the index."
  type        = number
  default     = 1024
}

variable "embedding_model_id" {
  description = "Bedrock embedding model ID (used when ai_provider = bedrock; also scopes the IAM model ARN)."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "gemini_embedding_model" {
  description = "Gemini embedding model (used when ai_provider = gemini); a leading \"models/\" is accepted."
  type        = string
  default     = "gemini-embedding-001"
}

variable "gemini_generation_model" {
  description = "Gemini generation model, e.g. \"gemini-3.5-flash-lite\". Required when ai_provider = gemini."
  type        = string
  default     = ""
}

variable "gemini_api_key_parameter_name" {
  description = "Name of the existing SSM SecureString holding the Gemini API key. Referenced by name only; Terraform never reads or stores the key."
  type        = string
  default     = "/rag-demo/gemini-api-key"
}

variable "chunk_size" {
  description = "Target chunk size in characters."
  type        = number
  default     = 1000
}

variable "chunk_overlap" {
  description = "Characters of overlap between consecutive chunks; must be smaller than chunk_size."
  type        = number
  default     = 150

  validation {
    condition     = var.chunk_overlap >= 0 && var.chunk_overlap < var.chunk_size
    error_message = "chunk_overlap must be >= 0 and smaller than chunk_size."
  }
}

variable "permissions_boundary_arn" {
  description = "ARN of a permissions boundary policy attached to the ingest and query roles (null = none). The CD deploy role can only create stack roles that carry it."
  type        = string
  default     = null
}

variable "web_dir" {
  description = "Directory with the demo site files (flat, text files only). __API_URL__ inside them is replaced with the /ask URL."
  type        = string
}

variable "cors_allowed_origins" {
  description = "Extra browser origins allowed to call the API, in addition to the CloudFront site. Wildcards are not allowed."
  type        = list(string)
  default     = ["http://localhost:8080"]

  validation {
    condition     = !contains(var.cors_allowed_origins, "*")
    error_message = "cors_allowed_origins must list explicit origins, not \"*\"."
  }
}

variable "ingest_max_concurrency" {
  description = "Max ingest Lambdas running in parallel (SQS event source mapping). Lower = gentler on the AI provider's rate limit. AWS requires >= 2."
  type        = number
  default     = 2

  validation {
    condition     = var.ingest_max_concurrency >= 2 && var.ingest_max_concurrency <= 1000
    error_message = "ingest_max_concurrency must be between 2 and 1000 (AWS limits)."
  }
}

variable "ingest_zip_path" {
  description = "Path to the ingest Lambda zip (build it first with scripts/build_lambdas)."
  type        = string
}

variable "generation_model_id" {
  description = "Bedrock model or inference profile ID for answer generation (Converse). Required when ai_provider = bedrock; ignored for gemini."
  type        = string
  default     = ""
}

variable "query_zip_path" {
  description = "Path to the query Lambda zip (build it first with scripts/build_lambdas)."
  type        = string
}

variable "top_k" {
  description = "Number of chunks retrieved per question."
  type        = number
  default     = 5
}

variable "distance_threshold" {
  description = "If the best cosine distance is above this, answer \"I don't know\" without calling the LLM."
  type        = number
  default     = 0.6
}

variable "max_output_tokens" {
  description = "Max tokens the generation model may produce per answer."
  type        = number
  default     = 512
}

variable "api_throttle_rate_limit" {
  description = "Steady-state requests per second allowed on the API."
  type        = number
  default     = 2
}

variable "api_throttle_burst_limit" {
  description = "Burst capacity for the API."
  type        = number
  default     = 5
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
