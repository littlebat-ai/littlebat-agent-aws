data "archive_file" "chat" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/chat"
  output_path = "${path.module}/.build/chat.zip"
  excludes    = ["__pycache__", "*.pyc", "requirements.txt"]
}

resource "aws_cloudwatch_log_group" "chat" {
  name              = "/aws/lambda/${local.name_prefix}-chat"
  retention_in_days = 30
}

resource "aws_lambda_function" "chat" {
  function_name    = "${local.name_prefix}-chat"
  role             = aws_iam_role.chat.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.chat.output_path
  source_code_hash = data.archive_file.chat.output_base64sha256
  memory_size      = 256
  timeout          = 30
  architectures    = ["arm64"]

  environment {
    variables = {
      AGENTCORE_RUNTIME_ARN = var.agentcore_runtime_arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.chat]
}
