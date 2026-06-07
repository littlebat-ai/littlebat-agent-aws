# ── Monthly spend budget ──────────────────────────────────────────────────────
# Alerts at 80% forecast; auto-attaches a deny-Bedrock policy to the
# AgentCore runtime role at 100% actual so spend can't run away.

resource "aws_budgets_budget" "monthly" {
  count = var.alert_email != "" ? 1 : 0

  name         = "${local.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}

# ── Deny-Bedrock policy (attached by budget action at 100% actual) ─────────────

resource "aws_iam_policy" "deny_bedrock" {
  count = var.alert_email != "" ? 1 : 0

  name = "${local.name_prefix}-deny-bedrock"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "DenyBedrock"
      Effect   = "Deny"
      Action   = ["bedrock:*", "bedrock-agentcore:*"]
      Resource = "*"
    }]
  })
}

data "aws_iam_policy_document" "budgets_action_assume" {
  count = var.alert_email != "" ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "budgets_action" {
  count = var.alert_email != "" ? 1 : 0

  name               = "${local.name_prefix}-budgets-action"
  assume_role_policy = data.aws_iam_policy_document.budgets_action_assume[0].json
}

data "aws_iam_policy_document" "budgets_action" {
  count = var.alert_email != "" ? 1 : 0

  statement {
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = [aws_iam_role.agentcore_runtime.arn]
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = [aws_iam_policy.deny_bedrock[0].arn]
    }
  }
}

resource "aws_iam_role_policy" "budgets_action" {
  count = var.alert_email != "" ? 1 : 0

  name   = "${local.name_prefix}-budgets-action"
  role   = aws_iam_role.budgets_action[0].id
  policy = data.aws_iam_policy_document.budgets_action[0].json
}

resource "aws_budgets_budget_action" "deny_agentcore_runtime" {
  count = var.alert_email != "" ? 1 : 0

  budget_name        = aws_budgets_budget.monthly[0].name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budgets_action[0].arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.deny_bedrock[0].arn
      roles      = [aws_iam_role.agentcore_runtime.name]
    }
  }

  subscriber {
    address           = var.alert_email
    subscription_type = "EMAIL"
  }
}
