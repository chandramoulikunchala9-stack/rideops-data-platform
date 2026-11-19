terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Local state by default (fine for a single developer). For a team, use a remote backend
  # with locking, e.g. (create the bucket once, by hand, outside this configuration):
  #
  # backend "s3" {
  #   bucket       = "rideops-terraform-state-<account-id>"
  #   key          = "rideops/dev/terraform.tfstate"
  #   region       = "ap-south-2"
  #   use_lockfile = true
  #   encrypt      = true
  # }
}

provider "aws" {
  region = var.aws_region

  # Every resource gets these tags, which drives cost allocation (project/workload/env).
  default_tags {
    tags = {
      project     = var.project
      environment = var.environment
      managed_by  = "terraform"
    }
  }
}
