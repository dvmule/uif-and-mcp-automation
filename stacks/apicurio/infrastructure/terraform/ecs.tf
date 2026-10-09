resource "aws_ecs_cluster" "this" {
  name = "${local.name}-cluster"

  # `shared`, not `registry`: this cluster hosts both the registry service
  # (ecs.tf) and the BFF service (bff.tf), so neither component owns it.
  tags = { component = "shared" }
}

resource "aws_cloudwatch_log_group" "registry" {
  name              = "/ecs/${local.name}-registry"
  retention_in_days = 30

  tags = { component = "registry" }
}

resource "aws_security_group" "registry" {
  name        = "${local.name}-registry-sg"
  description = "Apicurio registry Fargate task"
  vpc_id      = var.vpc_id

  tags = { component = "registry" }
}

resource "aws_vpc_security_group_ingress_rule" "registry_api_from_alb" {
  security_group_id            = aws_security_group.registry.id
  description                  = "Registry ALB to the API port, only the ALB"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.alb.id

  tags = { component = "registry" }
}

# The target group health-checks the management port, so the ALB must reach it.
# Traffic still only ever goes to 8080; 9000 serves /health/ready and /metrics.
resource "aws_vpc_security_group_ingress_rule" "registry_management_from_alb" {
  security_group_id            = aws_security_group.registry.id
  description                  = "Registry ALB to the management port for the health check"
  ip_protocol                  = "tcp"
  from_port                    = 9000
  to_port                      = 9000
  referenced_security_group_id = aws_security_group.alb.id

  tags = { component = "registry" }
}

# Kept wide, deliberately, and this is the one egress rule in the stack that is.
# The task has to reach quay.io through the NAT gateway to pull its image, the
# Secrets Manager interface endpoint to resolve the DB credential, CloudWatch
# Logs for awslogs, and the VPC resolver over UDP/53 for all three. Narrowing
# it to 443 plus 5432 is the obvious tightening and it drops DNS, at which
# point the image pull fails by name — the most common and least legible
# Fargate failure there is. Enumerating the real set (443 anywhere, 5432 to the
# DB group, 53 to the VPC CIDR) is a change worth making with a working
# deployment to test it against, not before the first apply.
# The task must keep UDP/53 to resolve quay.io; narrowing to
# 443+5432 breaks the image pull by name. There is no middle setting that clears this check,
# because it fires on the literal 0.0.0.0/0 and an enumerated rule would still need
# tcp/443 to the internet. Clearing it properly means mirroring the image into ECR and adding
# ECR api+dkr, S3 gateway and CloudWatch Logs endpoints, after which egress becomes
# 443 -> endpoint SGs, 53 -> VPC CIDR, 5432 -> db SG with no 0.0.0.0/0 at all. Tracked as a
# follow-up: it is worth doing against a deployment that exists to test it.
# trivy:ignore:AVD-AWS-0104 See the comment above.
resource "aws_vpc_security_group_egress_rule" "registry_all" {
  security_group_id = aws_security_group.registry.id
  description       = "Image pull, Secrets Manager, CloudWatch Logs, DNS"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"

  tags = { component = "registry" }
}

# A seat on the account's Secrets Manager interface endpoint, so the execution
# role can resolve the DB credential at task start.
#
# The NAT route in these subnets does not substitute for this. The endpoint has
# private DNS enabled, so secretsmanager.eu-west-1.amazonaws.com resolves
# VPC-wide to the endpoint's ENI and a task is never handed a public address to
# reach over NAT. Without this rule the fetch times out and the task never
# starts; the symptom is a timeout, not a denial, which is what distinguishes
# it from the KMS failure mode noted in rds.tf.
#
# The group is owned by the Echo/platform side (it is their Glue ENI group,
# reused as the endpoint's group), so this is an additive rule on someone
# else's resource. Beacon agreed the same arrangement for the same reason in
# stacks/beacon/infrastructure/terraform/lambda.tf, which is the precedent this
# follows rather than a new decision.
#
# replace_triggered_by is load-bearing: the deploy role can Authorize and Revoke
# rules on that group but NOT ec2:ModifySecurityGroupRules, so every change to
# this rule has to be a destroy-and-create. An in-place update is refused, and
# that refusal surfaces part-way through an apply.
resource "aws_vpc_security_group_ingress_rule" "registry_secretsmanager_access" {
  count                        = var.secretsmanager_endpoint_sg_id != "" ? 1 : 0
  security_group_id            = var.secretsmanager_endpoint_sg_id
  referenced_security_group_id = aws_security_group.registry.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  # AWS SG-rule descriptions reject '>' and only accept a-zA-Z0-9 and
  # . _-:/()#,@[]+=&;{}!$* -- no apostrophes, no em dashes.
  description = "apicurio registry task to secretsmanager vpc endpoint (UIFPE-36)"

  # `registry` even though the group belongs to echo: a rule takes the
  # component of what it exists for, and this exists only so the registry
  # task can fetch its DB credential.
  tags = { component = "registry" }

  lifecycle {
    replace_triggered_by = [aws_security_group.registry.id]
  }
}

resource "aws_ecs_task_definition" "registry" {
  family                   = "${local.name}-registry-task-definition"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.registry_cpu
  memory                   = var.registry_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name = "registry"
    # Digest, not tag -- see local.registry_image_ref and
    # variable "registry_image_digest". var.registry_image keeps the
    # human-readable version for anyone reading this file.
    image        = local.registry_image_ref
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = concat([
      { name = "APICURIO_STORAGE_KIND", value = "sql" },
      { name = "APICURIO_STORAGE_SQL_KIND", value = "postgresql" },
      { name = "APICURIO_DATASOURCE_URL", value = "jdbc:postgresql://${aws_db_instance.this.address}:5432/apicurio" },
      ], local.registry_deletion_enabled ? [
      # 3.3.2 ships apicurio.rest.artifact.deletion.enabled=false, so every
      # DELETE returns 405 — verified against the image, not assumed. Without
      # these, no cleanup path can work and the seed scripts cannot be re-run
      # idempotently, which is what Phase 5b of ../../DEPLOY-DEV.md needs.
      { name = "APICURIO_REST_DELETION_ARTIFACT_ENABLED", value = "true" },
      { name = "APICURIO_REST_DELETION_ARTIFACT_VERSION_ENABLED", value = "true" },
      { name = "APICURIO_REST_DELETION_GROUP_ENABLED", value = "true" },
    ] : [])
    # Native ECS secret injection, not beacon's self-fetch-at-runtime
    # pattern (iam.tf) — there is no application code of ours here to do
    # the fetching, since this is an off-the-shelf image.
    #
    # The secret is the one RDS owns (rds.tf's manage_master_user_password),
    # whose JSON carries the same `username` and `password` keys the
    # stack-owned secret did, so the :key:: syntax is unchanged.
    secrets = [
      { name = "APICURIO_DATASOURCE_USERNAME", valueFrom = "${aws_db_instance.this.master_user_secret[0].secret_arn}:username::" },
      { name = "APICURIO_DATASOURCE_PASSWORD", valueFrom = "${aws_db_instance.this.master_user_secret[0].secret_arn}:password::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.registry.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "registry"
      }
    }
    healthCheck = {
      # Port 9000, path /health/ready — NOT 8080/q/health/ready. Verified against
      # apicurio/apicurio-registry:3.3.2: Quarkus serves readiness on the
      # management interface (9000) at /health/ready, and every path under /q/
      # returns 404 on both ports. The previous command returned curl exit 22,
      # so the check could never pass and ECS would replace the task forever.
      command  = ["CMD-SHELL", "curl -f http://localhost:9000/health/ready || exit 1"]
      interval = 30
      timeout  = 5
      retries  = 3
    }
  }])

  tags = { component = "registry" }
}

resource "aws_ecs_service" "registry" {
  name            = "${local.name}-registry-service"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.registry.arn
  desired_count   = var.registry_desired_count
  launch_type     = "FARGATE"

  # Without these two, default_tags reaches the service OBJECT only: the running
  # tasks and their ENIs -- where all the Fargate spend is -- are created by the
  # service and inherit nothing. Same pair, same reason, as
  # stacks/beacon/infrastructure/terraform/keeper-gateway.tf.
  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  # The rollback this stack did not have. Without it a deploy that brings up a
  # task which never reaches a steady state leaves the service stuck retrying
  # while the workflow reports success, and nothing reverts the task
  # definition. With it, ECS gives up after its own failure threshold and puts
  # the previous task definition back on its own.
  #
  # `rollback = true` is the half that matters: `enable` alone stops the deploy
  # but leaves the broken revision current.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.registry.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.registry.arn
    container_name   = "registry"
    container_port   = 8080
  }

  tags = { component = "registry" }

  depends_on = [aws_lb_listener.registry]
}
