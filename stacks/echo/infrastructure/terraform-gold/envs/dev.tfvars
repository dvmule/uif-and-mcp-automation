environment           = "dev"
aws_region            = "eu-west-1"
gold_bucket           = "uif-echo-dev-gold"
gold_artifacts_bucket = "uif-echo-dev-gold-artifacts"

# UIFPE-543: who gets gold alert mail on day one. Seeded once; the
# Notifications page adds and removes the rest.
alert_seed_subscribers = ["reyasahamed.habibrahman@pearson.com"]

# Same placement as beacon's deliver Lambda (stacks/beacon/.../envs/dev.tfvars):
# gip-uif-nonprod-vpc-euw1, private self-service subnets az1/az2, and the
# shared Secrets Manager endpoint's security group.
vpc_id                        = "vpc-0287e62dc748ffab8"
private_subnet_ids            = ["subnet-059a43797031bfb59", "subnet-0084a87a82e01e8d8"]
secretsmanager_endpoint_sg_id = "sg-0b45c554b51638786"

# The gateway hop's identity check (not consumer auth, which is EntraID at
# the gateway). Dev and uat share the one non-production IES issuer.
gold_api_jwt_required     = true
gold_api_jwt_issuer       = "IES"
gold_api_jwt_jwks_url     = "https://int-piapi-internal.stg-openclass.com/tokens/jwks.json"
gold_api_jwt_token_type   = "at"
gold_api_jwt_algs         = ["RS512"]
gold_api_jwt_allowed_subs = ["ffffffff5be1a0d662817d79c40f6de6"]
