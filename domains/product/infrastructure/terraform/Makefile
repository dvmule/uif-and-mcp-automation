# Terraform wrapper — mirrors uif-relay-echo's infra Makefile conventions.
# Usage: make plan ENV=dev

SHELL := /bin/bash
# Valid environments come from the picklist, which lives with the gold
# control plane in the stack (ADR-0018). Path is relative to this Makefile.
ENVS  := $(shell python3 -c "import json; print(' '.join(json.load(open('../../../../stacks/echo/src/apps/environments.json', encoding='utf-8-sig'))['environments']))")

guard-env:
ifndef ENV
	$(error ENV is not set. Usage: make <target> ENV=dev)
endif
ifeq ($(filter $(ENV),$(ENVS)),)
	$(error ENV must be one of: $(ENVS). Got: $(ENV))
endif

TFVARS  := envs/$(ENV).tfvars
BACKEND := backends/$(ENV).s3.tfbackend

init: guard-env
	terraform init -reconfigure -backend-config=$(BACKEND)

plan: guard-env
	terraform plan -var-file=$(TFVARS) -out=$(ENV).tfplan

apply: guard-env
	terraform apply $(ENV).tfplan

fmt:
	terraform fmt -recursive

validate: guard-env
	terraform validate

.PHONY: guard-env init plan apply fmt validate
