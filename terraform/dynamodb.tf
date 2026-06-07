# ── Agent usage table ─────────────────────────────────────────────────────────
# Stores per-invocation token counts, cost estimates, and latency.
# Records auto-expire after 90 days via TTL.

resource "aws_dynamodb_table" "agent_usage" {
  name         = "${local.name_prefix}-agent-usage"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "date"
  range_key    = "ts"

  attribute {
    name = "date"
    type = "S"
  }
  attribute {
    name = "ts"
    type = "S"
  }
  attribute {
    name = "session_id"
    type = "S"
  }

  ttl {
    attribute_name = "expire_at"
    enabled        = true
  }

  global_secondary_index {
    name            = "session-index"
    hash_key        = "session_id"
    range_key       = "ts"
    projection_type = "ALL"
  }
}
