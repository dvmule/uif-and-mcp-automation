"""beacon_export.lambdas — the Step Functions flow's light executors (diagram
step 11): deliver (S3 channel copy), record_run (DynamoDB runs table),
emit_event (EventBridge completion/failure event).

Each module exposes handler(event, context) for Lambda. Deployment is one
zip containing the whole beacon package; the three functions differ only in
their handler string (beacon_export.lambdas.deliver.handler, ...). Heavy work
(Spark) never runs here — that is the ECS Fargate task's job.

Clients are lazy and injectable per call for tests (client=None kwarg).
"""
