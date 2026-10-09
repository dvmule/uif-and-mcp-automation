# UAT is not provisioned yet — names follow the echo-{env}-* convention.
environment           = "uat"
aws_region            = "eu-west-1"
gold_bucket           = "uif-echo-uat-gold"
gold_artifacts_bucket = "uif-echo-uat-gold-artifacts"

# Gold catalog source for the product change event pipeline (UIFPE-184):
# EDS convention echo_{env}_{layer}_{domain}.
gold_glue_database = "uif_echo_uat_gold_product"
gold_glue_table    = "product"

# The gateway hop's identity check (not consumer auth, which is EntraID at
# the gateway). Dev and uat share the one non-production IES issuer.
api_jwt_required     = true
api_jwt_issuer       = "IES"
api_jwt_jwks_url     = "https://int-piapi-internal.stg-openclass.com/tokens/jwks.json"
api_jwt_token_type   = "at"
api_jwt_algs         = ["RS512"]
api_jwt_allowed_subs = ["ffffffff5be1a0d662817d79c40f6de6"]
