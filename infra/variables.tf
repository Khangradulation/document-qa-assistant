variable "region" {
  type        = string
  description = "AWS Region for everything (S3 Vectors, Bedrock models and Lambda must all be available here)."
  default     = "us-east-1"
}

variable "app_name" {
  type    = string
  default = "document-qa-assistant"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.app_name))
    error_message = "app_name must be 3-31 chars: lowercase letters, digits, hyphens."
  }
}

variable "environment" {
  type    = string
  default = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.environment))
    error_message = "environment must be 2-10 lowercase alphanumerics."
  }
}

variable "embed_model_id" {
  type        = string
  description = "Bedrock embedding model. Must match the same model used at ingest and query time."
  default     = "amazon.titan-embed-text-v2:0"
}

variable "embed_dimensions" {
  type    = number
  default = 1024

  validation {
    condition     = contains([256, 512, 1024], var.embed_dimensions)
    error_message = "Titan Text Embeddings V2 supports 256, 512 or 1024 dimensions."
  }
}

variable "generation_model_id" {
  type        = string
  description = "Bedrock text model ID (or inference profile ID) enabled in YOUR account. No default on purpose."

  validation {
    condition     = length(var.generation_model_id) > 3
    error_message = "Set generation_model_id to a model enabled in your account."
  }
}

variable "generation_model_arns" {
  type        = list(string)
  description = "IAM resource ARNs the query Lambda may invoke for generation. Leave empty to use the foundation-model ARN of generation_model_id. If you use a cross-Region inference profile, list the profile ARN and the underlying model ARNs here."
  default     = []
}

variable "top_k" {
  type    = number
  default = 5
}

variable "max_distance" {
  type        = number
  description = "Chunks with vector distance above this are treated as irrelevant. Tune with the eval set."
  default     = 0.9
}

variable "extra_allowed_origins" {
  type        = list(string)
  description = "Extra browser origins for CORS (e.g. http://localhost:5173 for local dev). The CloudFront origin is always included."
  default     = []

  validation {
    condition     = alltrue([for o in var.extra_allowed_origins : o != "*" && can(regex("^https?://", o))])
    error_message = "Origins must be explicit http(s) origins; wildcards are not allowed."
  }
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "api_throttle_rate" {
  type        = number
  description = "Steady-state requests/second for the whole API (cost + abuse guard)."
  default     = 10
}

variable "api_throttle_burst" {
  type    = number
  default = 20
}

variable "ingest_reserved_concurrency" {
  type        = number
  description = "Cap on parallel ingestions. -1 = unreserved. New AWS accounts often cannot reserve concurrency; leave -1 until your quota is raised."
  default     = -1
}

variable "alert_email" {
  type        = string
  description = "Optional email for CloudWatch alarm notifications (you must confirm the subscription email)."
  default     = ""
}

variable "monthly_budget_usd" {
  type        = number
  description = "If > 0 and budget_email is set, creates an AWS Budget alert. Budgets notify; they do NOT cap spending."
  default     = 0
}

variable "budget_email" {
  type    = string
  default = ""
}

variable "force_destroy" {
  type        = bool
  description = "Allow terraform destroy to delete non-empty buckets (and their documents). Keep false except for dev teardown."
  default     = false
}

variable "permissions_boundary_arn" {
  type        = string
  description = "Optional IAM permissions boundary applied to the app's Lambda roles (created by infra/bootstrap)."
  default     = ""
}

variable "lambda_package" {
  type        = string
  description = "Path to the zip produced by scripts/build_lambda.py."
  default     = "build/lambda.zip"
}
