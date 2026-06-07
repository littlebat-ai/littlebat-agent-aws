# ── Cognito user pool ─────────────────────────────────────────────────────────

resource "aws_cognito_user_pool" "main" {
  name                = "${local.name_prefix}-users"
  deletion_protection = "INACTIVE"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  mfa_configuration = "OFF"

  user_pool_tier = "ESSENTIALS"

  password_policy {
    minimum_length                   = 8
    require_uppercase                = true
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  tags = {
    Project   = var.project_name
    Env       = var.env
    ManagedBy = "terraform"
  }
}

# ── Cognito hosted-UI domain ──────────────────────────────────────────────────

resource "aws_cognito_user_pool_domain" "main" {
  domain       = var.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.main.id
}

# ── iOS app client (PKCE, no secret) ─────────────────────────────────────────

resource "aws_cognito_user_pool_client" "ios" {
  name         = "${local.name_prefix}-ios"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false # public client — mobile apps cannot keep secrets

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = ["COGNITO"]
  enable_token_revocation      = true

  callback_urls = ["littlebat://callback"]
  logout_urls   = ["littlebat://logout"]

  # Token lifetimes
  access_token_validity  = 60 # minutes
  id_token_validity      = 60 # minutes
  refresh_token_validity = 30 # days

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  # Only allow PKCE + refresh — no SRP or USER_PASSWORD_AUTH
  explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]
}
