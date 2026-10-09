# Terraform wrapper for the gold root (ADR-0018 step 2) — same targets as the
# product domain's Makefile it was split from.
# Usage: make plan ENV=dev

SHELL := /bin/bash
# Valid environments come from the picklist, which lives with the gold
# control plane in the stack (ADR-0018). Path is relative to this Makefile.
ENVS  := $(shell python3 -c "import json; print(' '.join(json.load(open('../../src/apps/environments.json', encoding='utf-8-sig'))['environments']))")

guard-env:
ifndef ENV
	$(error ENV is not set. Usage: make <target> ENV=dev)
endif
ifeq ($(filter $(ENV),$(ENVS)),)
	$(error ENV must be one of: $(ENVS). Got: $(ENV))
endif

TFVARS  := envs/$(ENV).tfvars
BACKEND := backends/$(ENV).s3.tfbackend

# ── compile-preview Lambda bundle (UIFPE-219) ────────────────────────────────
# The one lambda with a third-party dependency: the Review step's query
# preview runs the REAL delta compiler, whose derivation battery needs
# sqlglot. archive_file can only zip a single directory, so this target
# stages handler + compiler modules + sqlglot into .build/ before plan.
# The sqlglot pin has ONE home — qe/requirements-dev.txt — read here so the
# deployed parser can never drift from the one the test suite verifies.
PREVIEW_SRC   := ../../src
PREVIEW_BUILD := .build/compile_preview_src
# The sqlglot pin still has one home, product's qe/requirements-dev.txt, until
# the gold test requirements move with the tests (follow-up).
SQLGLOT_PIN   := $(shell sed -n 's/^\(sqlglot==[0-9][0-9.]*\).*/\1/p' ../../../../domains/product/qe/requirements-dev.txt)

build-preview:
	@test -n "$(SQLGLOT_PIN)" || { echo "sqlglot pin not found in qe/requirements-dev.txt"; exit 1; }
	rm -rf $(PREVIEW_BUILD)
	mkdir -p $(PREVIEW_BUILD)
	cp $(PREVIEW_SRC)/apps/lambdas/compile_preview.py \
	   $(PREVIEW_SRC)/apps/cli/compile_intake_delta.py \
	   $(PREVIEW_SRC)/apps/cli/derivation_chain.py \
	   $(PREVIEW_SRC)/libs/gold_frequencies.py \
	   $(PREVIEW_SRC)/libs/gold_resources.py \
	   $(PREVIEW_BUILD)/
	python3 -m pip install --quiet --no-compile --no-deps \
	   --disable-pip-version-check --target $(PREVIEW_BUILD) $(SQLGLOT_PIN)

init: guard-env
	terraform init -reconfigure -backend-config=$(BACKEND)

plan: guard-env build-preview
	terraform plan -var-file=$(TFVARS) -out=$(ENV).tfplan

apply: guard-env
	terraform apply $(ENV).tfplan

fmt:
	terraform fmt -recursive

validate: guard-env
	terraform validate

.PHONY: guard-env init plan apply fmt validate build-preview
