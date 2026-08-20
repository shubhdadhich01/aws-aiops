###############################################################################
# modules/storage/outputs.tf
###############################################################################

output "bucket_name" {
  description = "Name of the application data bucket."
  value       = aws_s3_bucket.data.id
}

output "bucket_arn" {
  description = "ARN of the data bucket, for IAM policies in the caller."
  value       = aws_s3_bucket.data.arn
}

output "bucket_regional_domain_name" {
  description = "Regional domain name of the bucket, for anything that needs an endpoint rather than a name."
  value       = aws_s3_bucket.data.bucket_regional_domain_name
}

output "versioning_enabled" {
  description = "Whether object versioning is on. When true, the noncurrent-version expiration rule is what stops the bucket growing forever."
  value       = var.enable_versioning
}

output "table_name" {
  description = "Name of the DynamoDB table, or null when create_data_table is false."
  value       = var.create_data_table ? aws_dynamodb_table.data[0].name : null
}

output "table_arn" {
  description = "ARN of the DynamoDB table, or null when create_data_table is false."
  value       = var.create_data_table ? aws_dynamodb_table.data[0].arn : null
}

output "log_group_name" {
  description = "CloudWatch log group created for this module, with retention explicitly set."
  value       = aws_cloudwatch_log_group.data.name
}

output "protected_resources" {
  description = "Resources carrying prevent_destroy. `terraform destroy` on this environment WILL FAIL until these are handled deliberately — see teardown-checklist.md. That failure is the feature."
  value = compact([
    aws_s3_bucket.data.id,
    var.create_data_table ? aws_dynamodb_table.data[0].name : "",
  ])
}

output "estimated_monthly_cost_usd" {
  description = "Storage cost per month at lab volumes, us-east-1. Essentially free until real data lands in it."
  value = format(
    "%.2f",
    0.02 + (var.create_data_table ? 0.01 : 0.0) + (var.enable_point_in_time_recovery ? 0.05 : 0.0)
  )
}

output "cost_breakdown" {
  description = "Where the storage money goes, and where it silently grows."
  value = {
    s3_storage             = "~$0.02/month — S3 Standard at $0.023/GB-month. A lab bucket holds a handful of MB."
    s3_versioning          = var.enable_versioning ? "Included above, but UNBOUNDED without the lifecycle rule. Every overwrite keeps the old copy. Expiration is set to ${var.noncurrent_version_expiration_days} days." : "$0.00 — versioning disabled, and with it your ability to recover an overwritten object."
    s3_incomplete_uploads  = "$0.00 as configured — aborted after ${var.abort_incomplete_upload_days} days. Without this rule, failed uploads bill as storage forever and never appear in the object listing."
    dynamodb               = var.create_data_table ? "~$0.01/month idle — PAY_PER_REQUEST, so you pay $1.25 per million writes and $0.25 per million reads, plus $0.25/GB-month of rows." : "$0.00 — create_data_table is false."
    point_in_time_recovery = var.enable_point_in_time_recovery ? "~$0.05/month — $0.20/GB-month of table size. Cheap insurance." : "$0.00 — disabled. Restoring a corrupted table without it means restoring from a backup you did not take."
    cloudwatch_logs        = "$0.00 at lab volumes — $0.50/GB ingested, $0.03/GB-month stored, retention set to ${var.log_retention_days} days."
  }
}
