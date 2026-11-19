variable "environment" {
  description = "Environment name. Selects platform/<environment>.yml, the file the factory also reads."
  type        = string
  default     = "dev"
}

# The values below come from Snowflake. `factory platform aws-vars` reads them and writes
# snowflake.auto.tfvars.json, so nobody copies ARNs by hand.

variable "snowflake_iam_user_arn" {
  description = "STORAGE_AWS_IAM_USER_ARN of the storage integration: the Snowflake principal that assumes the role."
  type        = string
}

variable "snowflake_external_id" {
  description = "STORAGE_AWS_EXTERNAL_ID of the storage integration."
  type        = string
}

variable "snowpipe_sqs_arn" {
  description = "Notification channel of the auto-ingest pipes: the Snowflake-owned SQS queue for this bucket. Null until the first pipe exists."
  type        = string
  default     = null
}
