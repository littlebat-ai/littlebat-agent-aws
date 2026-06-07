variable "project_name" {
  description = "Short name used in resource names and tags."
  type        = string
  default     = "littlebat"
}

variable "env" {
  description = "Environment name (e.g. prod, dev)."
  type        = string
  default     = "prod"
}

variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "model_id" {
  description = "Bedrock model ID for the AgentCore runtime."
  type        = string
  default     = "us.amazon.nova-micro-v1:0"
}

variable "agentcore_runtime_arn" {
  description = "Full ARN of the Bedrock AgentCore runtime. Set after the runtime is created via the AWS console or CLI."
  type        = string
  default     = ""
}

variable "cognito_domain_prefix" {
  description = "Globally unique prefix for the Cognito Hosted UI domain. Results in <prefix>.auth.<region>.amazoncognito.com."
  type        = string
}

variable "alert_email" {
  description = "Email address to receive cost alert notifications."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly spend cap in USD. Alerts at 80% forecast; Bedrock blocked at 100% actual."
  type        = number
  default     = 20
}
