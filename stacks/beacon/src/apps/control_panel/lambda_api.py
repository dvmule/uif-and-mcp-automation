# lambda_api.py
# The control-panel API on Lambda (UIFPE-226) — the Echo pattern
# (API Gateway HTTP API -> zip Lambda), with one deliberate difference:
# Echo hand-rolls its routing in plain Python because it started that way
# with a handful of routes; this app arrives as a working FastAPI app
# (~40 routes plus the test suite that drives them through TestClient), so a
# two-line ASGI adapter beats a rewrite.
#
# Mangum translates the API Gateway proxy event (payload format 2.0, the
# $default catch-all route) into an ASGI request and the response back —
# nothing else changes: same app object, same routes, same middleware, same
# env contract as the ECS task (ecs.tf's environment list is the source of
# truth; apigateway.tf mirrors it).
#
# api_gateway_base_path is unset on purpose: CloudFront's strip-api-prefix
# function already removes /api before the request reaches the origin, so the
# app sees the same paths it sees behind the ALB and the Vite dev proxy.

from mangum import Mangum

from control_panel.api import app

handler = Mangum(app, lifespan="off")
