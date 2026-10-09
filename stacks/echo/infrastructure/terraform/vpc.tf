# vpc_endpoints.tf
# VPC endpoints that allow the Glue job (running inside a private subnet) to reach
# AWS services without needing a NAT Gateway or internet route.
#
# S3 and DynamoDB use free Gateway endpoints (route-table based, no hourly charge).
# Secrets Manager uses an Interface endpoint (PrivateLink, ~$7/month/AZ) — disabled
# by default. Enable it if your VPC has no NAT Gateway.

# Route table is looked up automatically from the subnet — no variable needed.
data "aws_route_table" "glue_subnet" {
  subnet_id = var.subnet_id
}




# ── S3 Gateway endpoint ───────────────────────────────────────────
resource "aws_vpc_endpoint" "s3" {
  count = var.create_s3_endpoint ? 1 : 0

  vpc_id            = data.aws_subnet.glue.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [data.aws_route_table.glue_subnet.id]

  tags = {
    Name        = "${local.name_prefix}-s3-endpoint"
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── DynamoDB Gateway endpoint ─────────────────────────────────────
resource "aws_vpc_endpoint" "dynamodb" {
  count = var.create_dynamodb_endpoint ? 1 : 0

  vpc_id            = data.aws_subnet.glue.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [data.aws_route_table.glue_subnet.id]

  tags = {
    Name        = "${local.name_prefix}-dynamodb-endpoint"
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── Secrets Manager Interface endpoint (optional) ─────────────────
# Required only when no NAT Gateway exists. Secrets Manager is called
# once per job run at Step 3 to fetch Oracle JDBC credentials.
# Enable by setting create_secretsmanager_endpoint = true in tfvars.
resource "aws_vpc_endpoint" "secretsmanager" {
  count = var.create_secretsmanager_endpoint ? 1 : 0

  vpc_id              = data.aws_subnet.glue.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [var.subnet_id]
  security_group_ids  = [aws_security_group.glue_eni.id]
  private_dns_enabled = true # rewrites secretsmanager.region.amazonaws.com to endpoint IP

  tags = {
    Name        = "${local.name_prefix}-secretsmanager-endpoint"
    Project     = local.project_tag
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}


