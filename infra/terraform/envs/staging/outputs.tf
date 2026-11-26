output "instance_id" {
  value = aws_instance.server.id
}

output "public_ip" {
  value = aws_eip.server.public_ip
}

output "host" {
  description = "Staging hostname (HTTPS)"
  value       = local.host
}

output "api_url" {
  value = "https://${local.host}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "deploy_bucket" {
  value = aws_s3_bucket.this["deploy"].bucket
}

output "backup_bucket" {
  value = aws_s3_bucket.this["backups"].bucket
}

output "media_bucket" {
  value = aws_s3_bucket.this["media"].bucket
}

output "admin_password_parameter" {
  description = "Where to find the initial admin password (AWS Console → Systems Manager → Parameter Store)"
  value       = aws_ssm_parameter.secret["ADMIN_PASSWORD"].name
}
