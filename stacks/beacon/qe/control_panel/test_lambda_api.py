# test_lambda_api.py
# The Lambda entrypoint (UIFPE-226): the SAME FastAPI app answering through
# the Mangum adapter, driven with real API Gateway HTTP API (payload 2.0)
# events — the exact shape the $default route delivers. No AWS.

import json
import unittest

from control_panel import lambda_api


def apigw_event(method: str, path: str, body: str | None = None) -> dict:
    """A minimal-but-faithful API Gateway v2 proxy event."""
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "example.execute-api.eu-west-1.amazonaws.com",
                    "content-type": "application/json"},
        "requestContext": {
            "accountId": "000000000000",
            "apiId": "example",
            "domainName": "example.execute-api.eu-west-1.amazonaws.com",
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1",
                     "sourceIp": "127.0.0.1", "userAgent": "pytest"},
            "requestId": "test",
            "stage": "$default",
            "time": "28/Aug/2026:00:00:00 +0000",
            "timeEpoch": 0,
        },
        "isBase64Encoded": False,
    }
    if body is not None:
        event["body"] = body
    return event


class TestLambdaEntrypoint(unittest.TestCase):
    def test_health_answers_through_the_adapter(self):
        response = lambda_api.handler(apigw_event("GET", "/health"), None)
        self.assertEqual(response["statusCode"], 200)
        payload = json.loads(response["body"])
        self.assertEqual(payload["status"], "ok")
        # the flags the UI header reads must survive the adapter
        for key in ("engine_available", "scheduling_enabled"):
            self.assertIn(key, payload)

    def test_unknown_route_is_a_json_404(self):
        response = lambda_api.handler(apigw_event("GET", "/definitely-not-a-route"), None)
        self.assertEqual(response["statusCode"], 404)

    def test_post_body_reaches_the_app(self):
        # /convert parses the JSON body — proves request bodies cross the
        # adapter, not just paths (422 without one)
        response = lambda_api.handler(
            apigw_event("POST", "/convert",
                        body=json.dumps({"sql": "SELECT p.ppid FROM gold_product.product p"})),
            None)
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("mappings", json.loads(response["body"]))

    def test_route_paths_carry_no_api_prefix(self):
        # CloudFront strips /api before the origin — a request that still
        # carries the prefix must NOT accidentally work, or local and deployed
        # behavior diverge
        response = lambda_api.handler(apigw_event("GET", "/api/health"), None)
        self.assertEqual(response["statusCode"], 404)


if __name__ == "__main__":
    unittest.main()
