# The event stack ships zero container images

This is the declared exemption `.github/workflows/stacks-ci.yml` looks for
in place of `src/Dockerfile`, per the root `CLAUDE.md` rule-3 contract —
the same position Echo and Beacon hold.

Every deployable in this stack is a zip or a script, packaged by Terraform
at apply time:

- **Three Lambdas** (`handler.py` — the pipeline controller,
  `subscription_provisioner.py`, `dlq_recorder.py`): single-file
  boto3+stdlib functions, shipped as deterministic zips staged to the
  release-artifacts bucket (`scripts/stage-release-artifacts.sh`). No dependencies
  beyond the Lambda runtime, so an image would add a build, a registry and
  a scan surface for zero benefit.
- **One Glue job** (`glue/event_fabric_processor.py`): a Python-shell
  script uploaded to S3; Glue owns the runtime. Glue jobs do not take
  container images on this path.

If any component ever grows real dependencies (a `requirements.txt` that
must be vendored), that is the moment to revisit — either a Lambda layer or
the one-image contract, decided then, not pre-built now.
