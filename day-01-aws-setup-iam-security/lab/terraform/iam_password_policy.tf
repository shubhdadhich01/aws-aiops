###############################################################################
# Account password policy
###############################################################################

resource "aws_iam_account_password_policy" "this" {
  minimum_password_length        = var.min_password_length
  require_uppercase_characters   = true
  require_lowercase_characters   = true
  require_numbers                = true
  require_symbols                = true
  allow_users_to_change_password = true
  max_password_age               = var.max_password_age_days
  password_reuse_prevention      = 24
  hard_expiry                    = false
}
