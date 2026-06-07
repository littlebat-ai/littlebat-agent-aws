output "chat_endpoint" {
  description = "POST /chat endpoint URL — set this in the iOS app Settings tab."
  value       = "${aws_apigatewayv2_api.agent.api_endpoint}/chat"
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID."
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "iOS app client ID — set this in the iOS app Settings tab."
  value       = aws_cognito_user_pool_client.ios.id
}

output "cognito_hosted_ui_domain" {
  description = "Base URL for the Cognito Hosted UI — set this in the iOS app Settings tab."
  value       = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}

output "agentcore_code_bucket" {
  description = "S3 bucket to upload the AgentCore runtime code package to."
  value       = aws_s3_bucket.agentcore_code.bucket
}

output "agentcore_runtime_role_arn" {
  description = "IAM role ARN to attach to the AgentCore runtime in the AWS console."
  value       = aws_iam_role.agentcore_runtime.arn
}

output "memories_bucket" {
  description = "S3 bucket for agent memory files and session logs."
  value       = aws_s3_bucket.memories.bucket
}

output "agent_usage_table" {
  description = "DynamoDB table for per-invocation usage records."
  value       = aws_dynamodb_table.agent_usage.name
}

output "device_access_key_id" {
  description = "AWS access key ID for the edge device (Pi) IAM user."
  value       = aws_iam_access_key.agent_device.id
}

output "device_secret_access_key" {
  description = "AWS secret access key for the edge device (Pi) IAM user."
  value       = aws_iam_access_key.agent_device.secret
  sensitive   = true
}
