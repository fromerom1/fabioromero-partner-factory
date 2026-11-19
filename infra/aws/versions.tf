terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = local.aws.region

  # Refuse to plan or apply with credentials of any other account.
  allowed_account_ids = [local.aws.account_id]

  default_tags {
    tags = {
      project     = "partner-factory"
      environment = lower(local.platform.environment)
      managed_by  = "terraform"
    }
  }
}
