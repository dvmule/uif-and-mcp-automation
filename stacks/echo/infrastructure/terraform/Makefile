# ── Usage ──────────────────────────────────────────────────────────
# FIRST TIME SETUP (run once, in order):
#   cd bootstrap && terraform init && terraform apply && cd ..
#   make migrate  ENV=dev       # copies local tfstate → S3, then delete local tfstate
#
# DAY-TO-DAY:
#   make init     ENV=dev       # re-init backend (needed when switching envs)
#   make plan     ENV=dev
#   make apply    ENV=dev
#   make destroy  ENV=dev  CONFIRM=yes
#   make fmt
#   make validate ENV=dev
#   make output   ENV=dev
#
# Credentials must be active in the shell before running any target.
# Use aws-runas or equivalent to populate AWS_ACCESS_KEY_ID etc.
# ───────────────────────────────────────────────────────────────────

SHELL := /bin/bash
# A `-uif` env is the new uif-echo-*-* stack being built beside the legacy one
# it replaces: dev-uif beside dev, uat-uif beside uat. Each has its own tfvars
# and its own backend key, so `make plan ENV=uat` and `make plan ENV=uat-uif`
# can never affect each other.
#
# This list and uif_echo_validate_env in
# ../deploy/_uif-echo-deploy-common.sh are the same set and must change
# together. They disagreed for uat-uif, and the failure was ordered badly: the
# shell check ran first, so `uif-echo-infra-deploy.sh uat-uif` died at
# preflight with a message about ENV, while anyone calling make directly got
# past that and stopped here instead.
ENVS  := dev dev-uif uat uat-uif prod

guard-env:
ifndef ENV
	$(error ENV is not set. Usage: make <target> ENV=dev)
endif
ifeq ($(filter $(ENV),$(ENVS)),)
	$(error ENV must be one of: $(ENVS). Got: $(ENV))
endif

TFVARS      := envs/$(ENV).tfvars
BACKEND_CFG := backends/$(ENV).hcl
PLAN_FILE   := .build/$(ENV).tfplan

.PHONY: init migrate plan apply destroy fmt validate output build-gcs-layer build-silver-bq-layer build-controller-bq-layer

## Switch backend to ENV and download providers.
## Safe to re-run — -reconfigure forces a clean init without migrating state.
init: guard-env
	terraform init \
	  -reconfigure \
	  -backend-config=$(BACKEND_CFG)

## ONE-TIME: copy existing local tfstate into S3, then remove local files.
## Run this instead of `init` the very first time you set up a new env.
migrate: guard-env
	terraform init \
	  -migrate-state \
	  -backend-config=$(BACKEND_CFG)
	@echo "State migrated. Run: rm terraform.tfstate terraform.tfstate.backup"

## Generate and save an execution plan. Does not apply anything.
## Terraform exits 2 when changes are pending — that is success for CI plan-only runs.
plan: guard-env
	@mkdir -p .build
	terraform plan \
	  -var-file=$(TFVARS) \
	  -out=$(PLAN_FILE) \
	  || test $$? -eq 2

## Apply the saved plan. Requires a prior `make plan ENV=...` run.
apply: guard-env
	@test -f $(PLAN_FILE) || (echo "ERROR: No plan file. Run 'make plan ENV=$(ENV)' first." && exit 1)
	terraform apply $(PLAN_FILE)

## Destroy all resources in ENV. Requires explicit CONFIRM=yes.
destroy: guard-env
ifndef CONFIRM
	$(error Destroy requires CONFIRM=yes — e.g. make destroy ENV=dev CONFIRM=yes)
endif
	terraform destroy -var-file=$(TFVARS)

## Format all .tf files in-place (run before committing).
fmt:
	terraform fmt -recursive .

## Validate config syntax and internal consistency.
validate: guard-env init
	terraform validate

## Show outputs for the active ENV state.
output: guard-env
	terraform output

## Build the gcs_replicator Lambda layer with google-cloud-storage + deps.
## Forces manylinux wheels so the layer is runnable in Lambda's python3.11 runtime.
## Output: ../lambda/gcs_layer.zip
build-gcs-layer:
	rm -rf ../lambda/gcs_layer ../lambda/gcs_layer.zip
	mkdir -p ../lambda/gcs_layer/python
	pip3 install \
	  --platform manylinux2014_x86_64 \
	  --implementation cp \
	  --python-version 3.11 \
	  --only-binary=:all: \
	  --upgrade \
	  --target ../lambda/gcs_layer/python \
	  google-cloud-storage==2.18.2
	cd ../lambda/gcs_layer && zip -qr ../gcs_layer.zip python
	@echo "Built ../lambda/gcs_layer.zip ($$(du -h ../lambda/gcs_layer.zip | cut -f1))"

## Build the silver_bq_replicator Lambda layer with google-cloud-storage,
## google-cloud-bigquery, google-auth, and fastavro. Forces manylinux wheels
## so the layer is runnable in Lambda's python3.11 runtime.
## Output: ../lambda/silver_bq_layer.zip
build-silver-bq-layer:
	rm -rf ../lambda/silver_bq_layer ../lambda/silver_bq_layer.zip
	mkdir -p ../lambda/silver_bq_layer/python
	pip3 install \
	  --platform manylinux2014_x86_64 \
	  --implementation cp \
	  --python-version 3.11 \
	  --only-binary=:all: \
	  --upgrade \
	  --target ../lambda/silver_bq_layer/python \
	  google-cloud-storage==2.18.2 \
	  google-cloud-bigquery==3.25.0 \
	  fastavro==1.9.4
	cd ../lambda/silver_bq_layer && zip -qr ../silver_bq_layer.zip python
	@echo "Built ../lambda/silver_bq_layer.zip ($$(du -h ../lambda/silver_bq_layer.zip | cut -f1))"

## Build the slim BQ Lambda layer for pipeline_controller. Contains only
## google-auth + rsa (pure-Python crypto fallback, no `cryptography` package),
## sized to fit alongside oracle_instant_client under Lambda's 250 MiB
## unzipped layers ceiling. pipeline_controller calls the BigQuery REST API
## directly via urllib3 (already in Lambda runtime via boto3), so the heavier
## google-cloud-bigquery client library is not needed here. The full bundle
## still lives in silver_bq_layer for silver_bq_replicator.
## Output: ../lambda/controller_bq_layer.zip
build-controller-bq-layer:
	rm -rf ../lambda/controller_bq_layer ../lambda/controller_bq_layer.zip
	mkdir -p ../lambda/controller_bq_layer/python
	pip3 install \
	  --platform manylinux2014_x86_64 \
	  --implementation cp \
	  --python-version 3.11 \
	  --only-binary=:all: \
	  --upgrade \
	  --target ../lambda/controller_bq_layer/python \
	  google-auth==2.30.0 \
	  rsa==4.9
	cd ../lambda/controller_bq_layer && zip -qr ../controller_bq_layer.zip python
	@echo "Built ../lambda/controller_bq_layer.zip ($$(du -h ../lambda/controller_bq_layer.zip | cut -f1))"
