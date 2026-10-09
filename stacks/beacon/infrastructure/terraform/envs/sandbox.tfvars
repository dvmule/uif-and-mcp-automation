# Sandbox rehearsal (account 061206535585): default VPC, public subnets,
# no NAT. Throwaway environment; terraform destroy after the rehearsal.
#
# The ECS/ALB-era knobs (assign_public_ip, api_desired_count, alb_internal,
# api_origin_protocol, trigger_enabled) were removed with their variables
# (UIFPE-206/226/215). api_gateway_domain still points at the retired
# sandbox ALB — clear or re-point it before any fresh sandbox apply.
env                = "sandbox"
region             = "us-east-1"
vpc_id             = "vpc-03f4c371bfb20b7a2"
private_subnet_ids = ["subnet-01a5510fffc819933", "subnet-00ed13a50c8c986a8"]
api_gateway_domain = "uif-beacon-sandbox-api-1832646482.us-east-1.elb.amazonaws.com"
