output "state_bucket_name" {
  description = "S3 bucket for Terraform state (use with -backend-config in infra/envs/demo)."
  value       = aws_s3_bucket.state.bucket
}

output "deploy_role_arn" {
  description = "Role for GitHub Actions to assume (store as a repository variable)."
  value       = aws_iam_role.deploy.arn
}

output "github_oidc_provider_arn" {
  description = "GitHub OIDC identity provider."
  value       = aws_iam_openid_connect_provider.github.arn
}

output "stack_boundary_arn" {
  description = "Permissions boundary every <prefix>-* stack role must carry."
  value       = aws_iam_policy.stack_boundary.arn
}
