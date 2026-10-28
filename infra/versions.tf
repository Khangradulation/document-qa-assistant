terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.60" # exact version is pinned by .terraform.lock.hcl (committed)
    }
  }

  # Remote state (created by infra/bootstrap). Partial config: supply the bucket at init:
  #   terraform init -backend-config="bucket=<state bucket>" -backend-config="region=us-east-1"
  # use_lockfile = S3-native locking (no DynamoDB lock table needed).
  backend "s3" {
    key          = "document-qa-assistant/dev/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Application = var.app_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
