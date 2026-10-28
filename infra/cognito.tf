# Users sign in with Cognito; API Gateway verifies the JWT, and the `sub` claim
# becomes the owner_id that scopes every document, vector and query.
resource "aws_cognito_user_pool" "users" {
  name                     = "${local.prefix}-users"
  deletion_protection      = "INACTIVE" # dev: allow terraform destroy
  mfa_configuration        = "OFF"      # demo; turn on (OPTIONAL/ON) for real use
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # No public sign-up: an admin creates users (see README). Prevents strangers
  # from creating accounts that spend your Bedrock budget.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${local.prefix}-web"
  user_pool_id = aws_cognito_user_pool.users.id

  generate_secret               = false # browser apps cannot keep secrets
  explicit_auth_flows           = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  access_token_validity         = 60
  id_token_validity             = 60
  refresh_token_validity        = 7
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}
