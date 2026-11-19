output "bucket" {
  description = "Landing bucket. Partners deliver under <partner>/landing/."
  value       = aws_s3_bucket.landing.id
}

output "snowflake_role_arn" {
  description = "Role referenced by the storage integration (STORAGE_AWS_ROLE_ARN)."
  value       = aws_iam_role.snowflake.arn
}

output "snowpipe_notification_enabled" {
  description = "False after the first apply; true once object-created events reach the Snowpipe queue."
  value       = var.snowpipe_sqs_arn != null
}
