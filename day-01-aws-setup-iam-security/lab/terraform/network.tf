###############################################################################
# EC2 security group
#
# SSH is the only inbound port. Ollama stays bound to 127.0.0.1 on the EC2
# instance, so TCP/11434 is intentionally NOT exposed.
###############################################################################

resource "aws_security_group" "aiops_runner" {
  name        = "${local.prefix}-aiops-runner-sg"
  description = "SSH access for the Day 01 AIOps runner; Ollama remains private."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Instructor SSH access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  egress {
    description = "Outbound access for package repositories, Git, Ollama model downloads, and AWS APIs"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
