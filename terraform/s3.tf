data "aws_caller_identity" "current" {}

# ── AgentCore code bucket ─────────────────────────────────────────────────────
# Stores the agent runtime code package uploaded by deploy_agent.py.

resource "aws_s3_bucket" "agentcore_code" {
  bucket = "${local.name_prefix}-agentcore-code-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "agentcore_code" {
  bucket                  = aws_s3_bucket.agentcore_code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "agentcore_code" {
  bucket = aws_s3_bucket.agentcore_code.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "agentcore_code" {
  bucket = aws_s3_bucket.agentcore_code.id
  versioning_configuration { status = "Enabled" }
}

# ── Memories bucket ───────────────────────────────────────────────────────────
# Stores per-user memory markdown files and per-session conversation JSONL logs.

resource "aws_s3_bucket" "memories" {
  bucket = "${local.name_prefix}-memories-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "memories" {
  bucket                  = aws_s3_bucket.memories.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "memories" {
  bucket = aws_s3_bucket.memories.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "memories" {
  bucket = aws_s3_bucket.memories.id
  versioning_configuration { status = "Enabled" }
}
