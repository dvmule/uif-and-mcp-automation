# ── The gold layer in uat: uif-echo-uat-* ──────────────────────────
#
# Paired with the main root's envs/uat-uif.tfvars, not with it. Two roots, two
# states, two files -- a tfvars is where resource NAMES come from, so two roots
# reading one file resolve to one set of names, which is not two roots
# coexisting.
#
# The file is named uat-uif to match the main root's, and `environment` is
# still "uat": this root has no name_prefix_override and computes
# `uif-echo-${var.environment}` unconditionally, so "uat" is what produces
# uif-echo-uat-*. The -uif in the filename distinguishes it from the retiring
# echo-uat-* stack, which this root never addresses.

environment = "uat"
aws_region  = "eu-west-1"

# ── The lake ───────────────────────────────────────────────────────
# Created by this root (s3.tf), not looked up.
gold_bucket           = "uif-echo-uat-gold"
gold_artifacts_bucket = "uif-echo-uat-gold-artifacts"

# ── Release artifacts ──────────────────────────────────────────────
# NOT OPTIONAL HERE, AND THE DEFAULT IS THE DANGEROUS VALUE. Empty means "no
# release has staged anything", which makes every Lambda fall back to its
# plan-time archive_file and every Glue job to the fixed prefix -- in other
# words, BUILDING IN UAT, which is the one thing the promotion model forbids
# ("uat receives artifacts, it never makes them").
#
# dev leaves it empty on purpose, because dev is the environment that builds.
# Every environment after dev must name its own bucket: its own, not dev's,
# even though dev's is readable from the same account -- a promote copies the
# bytes in and proves the digest, and reading dev's bucket would skip that.
release_artifacts_bucket = "uif-echo-uat-release-artifacts"

# ── Alerts ─────────────────────────────────────────────────────────
# Seeded once; the Notifications page adds and removes the rest. Same seed as
# dev, so a uat alarm is not silent on day one.
alert_seed_subscribers = ["reyasahamed.habibrahman@pearson.com"]

# ── VPC / networking ───────────────────────────────────────────────
# IDENTICAL TO dev.tfvars, AND THAT IS A FACT ABOUT THE VPC RATHER THAN A COPY.
# dev and uat share one nonprod VPC, gip-uif-nonprod-vpc-euw1
# (vpc-0287e62dc748ffab8) -- confirmed from the subnet the main root's
# uat-uif.tfvars names, subnet-038a8c6cc8c722470, which resolves to that VPC.
#
# The two subnets are the private self-service pair, az1 and az2:
#   subnet-059a43797031bfb59  gip-uif-nonprod-vpc-euw1-private-ss-az1
#   subnet-0084a87a82e01e8d8  gip-uif-nonprod-vpc-euw1-private-ss-az2
# The main root's uat sits on a private-glue subnet instead; this root stays on
# the self-service pair its dev jobs already run in, because nothing about uat
# changes which subnets gold wants and switching families would be a change
# with no reason behind it.
vpc_id             = "vpc-0287e62dc748ffab8"
private_subnet_ids = ["subnet-059a43797031bfb59", "subnet-0084a87a82e01e8d8"]

# THE SAME SECURITY GROUP AS dev, NECESSARILY. AWS permits only ONE interface
# endpoint per service per VPC with private DNS enabled, and this VPC's
# Secrets Manager endpoint is vpce-0742f8ea5aa68967e, whose security group is
# sg-0b45c554b51638786 -- named `echo-uat-glue-eni`, because envs/uat.tfvars
# set create_secretsmanager_endpoint = true and the live echo-uat stack owns it.
#
# So gold in every environment of this VPC reaches Secrets Manager through one
# endpoint behind one group. alerts.tf adds an ADDITIVE ingress rule to it from
# gold's OWN security group, which is name-prefixed, so uat's rule is a
# different rule from dev's and the two cannot collide.
#
# WORTH A PERSON'S EYE ON THE FIRST APPLY: that group belongs to another
# stack's root. The main root's uat-uif.tfvars flags the same dependency as
# unresolved and "a manual, cross-stack change on a LIVE stack's SG ... it
# belongs in phase 4 with a person watching, and GetSecretValue is what fails
# if it is missed". For gold the rule is declared rather than manual, and it is
# already in place for dev -- but it is still this root writing a rule into a
# security group it does not own.
secretsmanager_endpoint_sg_id = "sg-0b45c554b51638786"

# The gateway hop's identity check (not consumer auth, which is EntraID at
# the gateway). Dev and uat share the one non-production IES issuer.
gold_api_jwt_required     = true
gold_api_jwt_issuer       = "IES"
gold_api_jwt_jwks_url     = "https://int-piapi-internal.stg-openclass.com/tokens/jwks.json"
gold_api_jwt_token_type   = "at"
gold_api_jwt_algs         = ["RS512"]
gold_api_jwt_allowed_subs = ["ffffffff5be1a0d662817d79c40f6de6"]
