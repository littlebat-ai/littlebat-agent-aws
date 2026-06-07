data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ── AgentCore runtime execution role ──────────────────────────────────────────

data "aws_iam_policy_document" "agentcore_runtime_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:*:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "agentcore_runtime" {
  name               = "${local.name_prefix}-agentcore-runtime"
  assume_role_policy = data.aws_iam_policy_document.agentcore_runtime_assume.json
}

data "aws_iam_policy_document" "agentcore_runtime" {
  statement {
    sid    = "BedrockInvoke"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "S3CodeRead"
    effect = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.agentcore_code.arn,
      "${aws_s3_bucket.agentcore_code.arn}/*",
    ]
  }

  statement {
    sid    = "MemoriesAccess"
    effect = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.memories.arn,
      "${aws_s3_bucket.memories.arn}/*",
    ]
  }

  statement {
    sid    = "UsageTable"
    effect = "Allow"
    actions = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"]
    resources = [
      aws_dynamodb_table.agent_usage.arn,
      "${aws_dynamodb_table.agent_usage.arn}/index/*",
    ]
  }

  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }

  statement {
    sid    = "Metrics"
    effect = "Allow"
    actions = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }

  statement {
    sid    = "XRay"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "agentcore_runtime" {
  name   = "${local.name_prefix}-agentcore-runtime"
  role   = aws_iam_role.agentcore_runtime.id
  policy = data.aws_iam_policy_document.agentcore_runtime.json
}

# ── IAM user for Raspberry Pi / edge device ───────────────────────────────────

resource "aws_iam_user" "agent_device" {
  name = "${local.name_prefix}-agent-device"
  path = "/"
}

resource "aws_iam_access_key" "agent_device" {
  user = aws_iam_user.agent_device.name
}

data "aws_iam_policy_document" "agent_device" {
  statement {
    sid     = "AgentCoreInvoke"
    effect  = "Allow"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [
      "arn:aws:bedrock-agentcore:*:${data.aws_caller_identity.current.account_id}:runtime/*",
    ]
  }

  statement {
    sid     = "Transcribe"
    effect  = "Allow"
    actions = ["transcribe:StartStreamTranscription"]
    resources = ["*"]
  }

  statement {
    sid     = "Polly"
    effect  = "Allow"
    actions = ["polly:SynthesizeSpeech"]
    resources = ["*"]
  }

  statement {
    sid     = "MemoriesRead"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.memories.arn,
      "${aws_s3_bucket.memories.arn}/*",
    ]
  }
}

resource "aws_iam_policy" "agent_device" {
  name   = "${local.name_prefix}-agent-device"
  policy = data.aws_iam_policy_document.agent_device.json
}

resource "aws_iam_user_policy_attachment" "agent_device" {
  user       = aws_iam_user.agent_device.name
  policy_arn = aws_iam_policy.agent_device.arn
}

# ── Chat Lambda IAM ───────────────────────────────────────────────────────────

resource "aws_iam_role" "chat" {
  name               = "${local.name_prefix}-chat"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "chat_basic" {
  role       = aws_iam_role.chat.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "chat" {
  statement {
    sid     = "AgentCoreInvoke"
    effect  = "Allow"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [
      "arn:aws:bedrock-agentcore:*:${data.aws_caller_identity.current.account_id}:runtime/*",
    ]
  }
}

resource "aws_iam_policy" "chat" {
  name   = "${local.name_prefix}-chat"
  policy = data.aws_iam_policy_document.chat.json
}

resource "aws_iam_role_policy_attachment" "chat_agentcore" {
  role       = aws_iam_role.chat.name
  policy_arn = aws_iam_policy.chat.arn
}
