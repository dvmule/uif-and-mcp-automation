# Prod is not provisioned yet — names follow the echo-{env}-* convention.
environment           = "prod"
aws_region            = "eu-west-1"
gold_bucket           = "uif-echo-prod-gold"
gold_artifacts_bucket = "uif-echo-prod-gold-artifacts"

# Gold catalog source for the product change event pipeline (UIFPE-184):
# EDS convention echo_{env}_{layer}_{domain}.
gold_glue_database = "uif_echo_prod_gold_product"
gold_glue_table    = "product"

# The gateway hop's identity check. The production IES issuer, with NO
# subject yet:
# every request is refused until one is added, which is the intended posture
# for an environment whose callers have not been agreed.
api_jwt_required     = true
api_jwt_issuer       = "IES"
api_jwt_jwks_url     = "https://piapi-internal.openclass.com/tokens/jwks.json"
api_jwt_token_type   = "at"
api_jwt_algs         = ["RS512"]
api_jwt_allowed_subs = []
