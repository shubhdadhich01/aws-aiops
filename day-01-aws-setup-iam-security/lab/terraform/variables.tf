variable "aws_region" {
  description = "AWS region for the Day 01 lab."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix applied to all Day 01 resources."
  type        = string
  default     = "cbc-day01"
}

variable "min_password_length" {
  description = "Minimum IAM console password length."
  type        = number
  default     = 14
}

variable "max_password_age_days" {
  description = "Maximum IAM console password age."
  type        = number
  default     = 90
}

variable "allowed_regions" {
  description = "AWS regions allowed by the developer policy."
  type        = list(string)
  default     = ["us-east-1"]
}

variable "alert_email" {
  description = "Email address for AWS Budget notifications."
  type        = string
}

variable "budget_limit_usd" {
  description = "Monthly budget limit in USD."
  type        = number
  default     = 10
}

variable "create_bad_policy" {
  description = "Create deliberately insecure IAM policy and open-trust role as audit fixtures."
  type        = bool
  default     = true
}

variable "require_audit_role_mfa" {
  description = "Require MFA in the security-audit role trust policy. Keep false for automatic EC2 role assumption."
  type        = bool
  default     = false
}

variable "allowed_ssh_cidr" {
  description = "Your public IP/CIDR allowed to SSH to the AIOps EC2 runner, for example 203.0.113.10/32."
  type        = string
}

variable "aiops_key_name" {
  description = "Existing EC2 key pair name used for SSH access."
  type        = string
}

variable "aiops_instance_type" {
  description = "EC2 size for Python + Ollama + Qwen3:8b."
  type        = string
  default     = "m7i.xlarge"
}

variable "aiops_root_volume_gb" {
  description = "Root EBS size for the AIOps runner and local model cache."
  type        = number
  default     = 40
}

variable "aiops_subnet_id" {
  description = "Optional subnet ID. Empty selects the first subnet in the default VPC."
  type        = string
  default     = ""
}

variable "git_repository_url" {
  description = "Git repository containing iam_aiops_audit.py and requirements.txt. Public repo recommended for the demo."
  type        = string
  default     = ""
}

variable "git_branch" {
  description = "Git branch to clone for the bootcamp runner."
  type        = string
  default     = "main"
}

variable "ollama_model" {
  description = "Ollama model pulled during EC2 bootstrap."
  type        = string
  default     = "qwen3:8b"
}

variable "owner" {
  description = "Owner name/tag applied to supported AWS resources."
  type        = string
  default     = "bootcamp"
}