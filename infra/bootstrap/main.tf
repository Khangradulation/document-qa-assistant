# One-time setup, run by a human with admin rights (NOT by CI):
#   - encrypted, versioned S3 bucket for Terraform state (S3-native locking)
#   - GitHub OIDC identity provider (lets Actions assume roles WITHOUT stored keys)
#   - a read-mostly "plan" role and a scoped "deploy" role
#   - a permissions boundary that caps what any role the deploy role creates can do
# Its own state is LOCAL and git-ignored (it only manages these few resources).
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.60"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Application = var.app_name, ManagedBy = "terraform-bootstrap" }
  }
}

variable "region" {
  type    = string
  default = "us-east-2"
}

variable "app_name" {
  type    = string
  default = "document-qa-assistant"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "github_repository" {
  type        = string
  description = "owner/repo allowed to assume the roles, e.g. octocat/document-qa-assistant"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "Use the form owner/repo."
  }
}

variable "github_environment" {
  type        = string
  description = "GitHub Environment (with required reviewers) whose jobs may assume the DEPLOY role."
  default     = "dev"
}

variable "github_teardown_environment" {
  type        = string
  description = "Separate GitHub Environment (own reviewers) for the destructive teardown workflow."
  default     = "dev-teardown"
}

variable "create_oidc_provider" {
  type        = bool
  description = "An account can have only one GitHub OIDC provider. Set false if it already exists."
  default     = true
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = "${var.app_name}-${var.environment}"
  state_bkt  = "${var.app_name}-tfstate-${local.account_id}"
  oidc_arn = (
    var.create_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : data.aws_iam_openid_connect_provider.github[0].arn
  )
}

# ---- Terraform state bucket ------------------------------------------------
resource "aws_s3_bucket" "state" {
  bucket = local.state_bkt
  #checkov:skip=CKV_AWS_18:Access logging omitted to save cost.
  #checkov:skip=CKV_AWS_144:Cross-region replication out of scope.
  #checkov:skip=CKV2_AWS_62:No notifications needed.
  #checkov:skip=CKV_AWS_145:SSE-S3 used instead of a customer KMS key.
  #checkov:skip=CKV2_AWS_61:Old state versions are expired by the rule below.
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 90 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.state_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# ---- GitHub OIDC provider --------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

# ---- Trust policies: WHO may assume each role -----------------------------
# The `sub` claim names the repo AND the environment/branch, so a fork, another
# repo, or an unapproved branch cannot assume these roles.
data "aws_iam_policy_document" "trust_deploy" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:environment:${var.github_environment}",
        "repo:${var.github_repository}:environment:${var.github_teardown_environment}",
      ]
    }
  }
}

data "aws_iam_policy_document" "trust_plan" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:pull_request",
        "repo:${var.github_repository}:ref:refs/heads/main",
      ]
    }
  }
}

# ---- Permissions boundary for roles the deploy role creates ---------------
data "aws_iam_policy_document" "boundary" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.prefix}-*:*"]
  }
  statement {
    sid       = "AppObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${local.prefix}-docs-${local.account_id}/uploads/*"]
  }
  statement {
    sid       = "AppTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query"]
    resources = ["arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*"]
  }
  statement {
    sid       = "AppVectors"
    actions   = ["s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:QueryVectors", "s3vectors:DeleteVectors"]
    resources = ["arn:aws:s3vectors:${var.region}:${local.account_id}:bucket/${local.prefix}-*/index/*"]
  }
  statement {
    sid       = "Models"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:*:${local.account_id}:inference-profile/*"]
  }
  statement {
    sid       = "InvokeOwnFunctions"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${local.account_id}:function:${local.prefix}-*"]
  }
}

resource "aws_iam_policy" "boundary" {
  name   = "${local.prefix}-permissions-boundary"
  policy = data.aws_iam_policy_document.boundary.json
}

# ---- PLAN role: read configuration, never user data -----------------------
resource "aws_iam_role" "plan" {
  name                 = "${local.prefix}-gha-plan"
  assume_role_policy   = data.aws_iam_policy_document.trust_plan.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "plan_extra" {
  statement {
    sid     = "StateAndLock"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.state.arn}/*.tflock",
      "${aws_s3_bucket.state.arn}/${var.app_name}/*",
    ]
  }
  statement {
    sid       = "ListState"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  # ReadOnlyAccess can read data too. Explicitly deny reading user content.
  statement {
    sid       = "DenyReadingUserData"
    effect    = "Deny"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["arn:aws:s3:::${local.prefix}-docs-${local.account_id}/*"]
  }
  statement {
    sid       = "DenyReadingRecords"
    effect    = "Deny"
    actions   = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = ["arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*"]
  }
  statement {
    sid       = "DenyReadingVectors"
    effect    = "Deny"
    actions   = ["s3vectors:GetVectors", "s3vectors:QueryVectors", "s3vectors:ListVectors"]
    resources = ["*"]
  }
  statement {
    sid       = "DenyReadingSecretsAndKeys" # plan never needs these; keeps a hijacked PR job from reading them
    effect    = "Deny"
    actions   = ["secretsmanager:GetSecretValue", "ssm:GetParameter*", "kms:Decrypt"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "plan_extra" {
  name   = "state-and-denies"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan_extra.json
}

# ---- DEPLOY role: create/update ONLY resources named ${prefix}-* ----------
resource "aws_iam_role" "deploy" {
  name                 = "${local.prefix}-gha-deploy"
  assume_role_policy   = data.aws_iam_policy_document.trust_deploy.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  #checkov:skip=CKV_AWS_111:Write actions are limited to resources named with this project prefix; IAM writes also require the permissions boundary.
  #checkov:skip=CKV_AWS_356:"*" remains only for actions AWS cannot scope by resource (CloudFront, log delivery, read-only lookups).
  #checkov:skip=CKV_AWS_109:Role management is limited to prefixed roles that must carry the permissions boundary (see CreateRolesWithBoundaryOnly / NeverRemoveBoundary).
  statement {
    sid       = "StateBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/${var.app_name}/*", "${aws_s3_bucket.state.arn}/*.tflock"]
  }
  statement {
    sid       = "ListState"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    sid     = "AppBuckets"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::${local.prefix}-*",
      "arn:aws:s3:::${local.prefix}-*/*",
    ]
  }
  statement {
    sid       = "AppVectors"
    actions   = ["s3vectors:*"]
    resources = ["arn:aws:s3vectors:${var.region}:${local.account_id}:bucket/${local.prefix}-*", "arn:aws:s3vectors:${var.region}:${local.account_id}:bucket/${local.prefix}-*/index/*"]
  }
  statement {
    sid       = "AppTable"
    actions   = ["dynamodb:*"]
    resources = ["arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*"]
  }
  statement {
    sid       = "AppFunctions"
    actions   = ["lambda:*"]
    resources = ["arn:aws:lambda:${var.region}:${local.account_id}:function:${local.prefix}-*"]
  }
  statement {
    sid       = "AppLogs"
    actions   = ["logs:*"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.prefix}-*", "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.prefix}-*:*", "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/apigateway/${local.prefix}-*", "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/apigateway/${local.prefix}-*:*"]
  }
  statement {
    sid       = "LogDeliveryForApiAccessLogs" # API Gateway access logging requires these on "*"
    actions   = ["logs:CreateLogDelivery", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
    resources = ["*"]
  }
  statement {
    sid       = "AppApi"
    actions   = ["apigateway:*"]
    resources = ["arn:aws:apigateway:${var.region}::/apis", "arn:aws:apigateway:${var.region}::/apis/*", "arn:aws:apigateway:${var.region}::/tags/*"]
  }
  statement {
    sid       = "Cognito" # user pools cannot be scoped by name in IAM
    actions   = ["cognito-idp:*"]
    resources = ["arn:aws:cognito-idp:${var.region}:${local.account_id}:userpool/*"]
  }
  statement {
    sid = "CloudFront" # CloudFront actions do not support name-based scoping, so list only what is needed
    actions = [
      "cloudfront:CreateDistribution", "cloudfront:GetDistribution", "cloudfront:UpdateDistribution",
      "cloudfront:DeleteDistribution", "cloudfront:ListDistributions", "cloudfront:TagResource",
      "cloudfront:UntagResource", "cloudfront:ListTagsForResource", "cloudfront:CreateInvalidation",
      "cloudfront:CreateOriginAccessControl", "cloudfront:GetOriginAccessControl",
      "cloudfront:UpdateOriginAccessControl", "cloudfront:DeleteOriginAccessControl",
      "cloudfront:ListOriginAccessControls", "cloudfront:GetCachePolicy", "cloudfront:ListCachePolicies",
      "cloudfront:GetResponseHeadersPolicy", "cloudfront:ListResponseHeadersPolicies",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "AlarmsTopicsBudgets"
    actions   = ["cloudwatch:*Alarm*", "cloudwatch:TagResource", "cloudwatch:ListTagsForResource", "sns:*", "budgets:*"]
    resources = ["arn:aws:cloudwatch:${var.region}:${local.account_id}:alarm:${local.prefix}-*", "arn:aws:sns:${var.region}:${local.account_id}:${local.prefix}-*", "arn:aws:budgets::${local.account_id}:budget/${local.prefix}-*"]
  }
  statement {
    sid       = "ReadOnlyLookups"
    actions   = ["sts:GetCallerIdentity", "cloudwatch:DescribeAlarms", "iam:GetPolicy", "iam:ListOpenIDConnectProviders"]
    resources = ["*"]
  }
  # IAM: only ${prefix}-* roles, and a new role MUST carry the permissions boundary.
  statement {
    sid       = "CreateRolesWithBoundaryOnly"
    actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
    resources = ["arn:aws:iam::${local.account_id}:role/${local.prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary.arn]
    }
  }
  statement {
    sid = "ManageAppRoles"
    actions = [
      "iam:GetRole", "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy",
      "iam:TagRole", "iam:UntagRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/${local.prefix}-*"]
  }
  statement {
    sid       = "PassRolesToLambdaOnly"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/${local.prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }
  statement {
    sid       = "NeverRemoveBoundary"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "plan_role_arn" {
  value = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}

output "permissions_boundary_arn" {
  value = aws_iam_policy.boundary.arn
}
