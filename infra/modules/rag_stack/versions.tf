terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # aws_s3vectors_* resources first appeared in 6.24.
      version = ">= 6.24"
    }
  }
}
