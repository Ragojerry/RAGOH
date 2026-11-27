variable "region" {
  description = "AWS region (from the AWS_REGION GitHub variable)"
  type        = string
  default     = "eu-north-1"
}

variable "instance_type" {
  description = "Pilot server size. t3.small = 2 vCPU / 2 GB (enough for API + DB + Redis for a 6-person pilot)."
  type        = string
  default     = "t3.small"
}

variable "disk_gb" {
  description = "Root disk size (database, images, map data)."
  type        = number
  default     = 30
}

variable "domain_name" {
  description = "Optional own domain (e.g. api.ragoh.example.org). Empty = free <ip>.sslip.io hostname."
  type        = string
  default     = ""
}

variable "backup_retention_days" {
  description = "How long nightly database backups are kept in S3."
  type        = number
  default     = 30
}
