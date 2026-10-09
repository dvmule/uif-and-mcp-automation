# test_pearson_notifications.py
# The shared Pearson Notifications client (UIFPE-543, lifted from beacon's
# UIFPE-444 adapter): the token lifecycle (mint, cache, re-mint on 401), the
# event payload contract, refusal as a readable error, and the stdlib-only
# rule that lets one file serve a Lambda zip and a Glue job alike.

import ast
import json
import unittest
from pathlib import Path

from pearson_notifications import NotificationsClient, event_id
from pearson_notifications import client as client_module

# Lives in echo's gold suite, not under platform/: pytest's importlib mode
# would register a module named "platform" for a test there and shadow the
# standard library's (attrs, and so jsonschema, break on collection).
PACKAGE = (Path(__file__).resolve().parents[4] / "platform" / "python"
           / "pearson_notifications")


class FakeSecrets:
    def __init__(self):
        self.value = {"client_id": "cid", "client_secret": "cs"}
        self.reads = 0

    def get_secret_value(self, SecretId):
        self.reads += 1
        return {"SecretString": json.dumps(self.value)}


class FakePost:
    """Scripted (status, body) responses per URL suffix, recording calls."""

    def __init__(self):
        self.token_responses = [(200, {"data": "tok-1", "expires_in": 300})]
        self.event_responses = [(201, {"eventId": "evt-1"})]
        self.calls = []

    def __call__(self, url, *, data, headers):
        self.calls.append({"url": url, "data": data, "headers": headers})
        if "token" in url:
            return self.token_responses.pop(0)
        return self.event_responses.pop(0)


def make_client(post=None, secrets=None, now=None):
    client_module._token_cache.clear()
    return NotificationsClient(
        secrets or FakeSecrets(),
        secret_ref="uif/beacon/dev/notifications/api",
        api_url="https://notifications-api.example/",
        token_url="https://pi.example/tokens",
        post=post or FakePost(), now=now or (lambda: 1000.0))


def send(client, recipients=("a@b.co",)):
    return client.send_email(recipients=list(recipients), subject="s",
                             body_html="<p>b</p>")


class TestTokenLifecycle(unittest.TestCase):
    def test_mints_once_and_caches(self):
        post, secrets = FakePost(), FakeSecrets()
        post.event_responses = [(201, {"eventId": "1"}), (201, {"eventId": "2"})]
        client = make_client(post, secrets)
        send(client)
        send(client)
        token_calls = [c for c in post.calls if "token" in c["url"]]
        self.assertEqual(len(token_calls), 1)
        self.assertEqual(secrets.reads, 1)
        payload = json.loads(token_calls[0]["data"])
        # client_id / client_secret CARRY the PI userName / password
        self.assertEqual(payload, {"userName": "cid", "password": "cs"})

    def test_expired_cache_re_mints(self):
        clock = {"t": 1000.0}
        post = FakePost()
        post.token_responses = [(200, {"data": "tok-1", "expires_in": 100}),
                                (200, {"data": "tok-2", "expires_in": 100})]
        post.event_responses = [(201, {}), (201, {})]
        client = make_client(post, now=lambda: clock["t"])
        send(client)
        clock["t"] += 100  # past expiry minus slack
        send(client)
        self.assertEqual([c["headers"]["X-Authorization"] for c in post.calls
                          if "events" in c["url"]], ["tok-1", "tok-2"])

    def test_401_re_mints_once_and_retries(self):
        post = FakePost()
        post.token_responses = [(200, {"data": "tok-1"}), (200, {"data": "tok-2"})]
        post.event_responses = [(401, {"message": "expired"}), (201, {"eventId": "evt-2"})]
        client = make_client(post)
        response = send(client)
        self.assertEqual(event_id(response), "evt-2")
        events = [c for c in post.calls if "events" in c["url"]]
        self.assertEqual([c["headers"]["X-Authorization"] for c in events], ["tok-1", "tok-2"])

    def test_a_second_401_is_fatal_not_a_loop(self):
        post = FakePost()
        post.token_responses = [(200, {"data": "tok-1"}), (200, {"data": "tok-2"})]
        post.event_responses = [(401, {}), (401, {})]
        with self.assertRaises(RuntimeError):
            send(make_client(post))
        self.assertEqual(len([c for c in post.calls if "events" in c["url"]]), 2)

    def test_wrapped_data_shape_tolerated(self):
        post = FakePost()
        post.token_responses = [(200, {"data": {"token": "tok-w"}})]
        send(make_client(post))
        events = [c for c in post.calls if "events" in c["url"]]
        self.assertEqual(events[0]["headers"]["X-Authorization"], "tok-w")

    def test_token_refusal_is_a_readable_error(self):
        post = FakePost()
        post.token_responses = [(403, {"error": "bad creds"})]
        with self.assertRaises(RuntimeError) as ctx:
            send(make_client(post))
        self.assertIn("PI tokens endpoint refused", str(ctx.exception))
        self.assertIn("HTTP 403", str(ctx.exception))

    def test_default_ttl_when_the_response_has_none(self):
        post = FakePost()
        post.token_responses = [(200, {"data": "tok-1"})]
        client = make_client(post, now=lambda: 1000.0)
        send(client)
        self.assertEqual(client_module._token_cache["uif/beacon/dev/notifications/api"]["expires_at"],
                         1000.0 + client_module._DEFAULT_TOKEN_TTL_SECONDS)


class TestEventContract(unittest.TestCase):
    def test_event_payload_shape(self):
        post = FakePost()
        client = make_client(post)
        client.send_email(recipients=["a@b.co", "c@d.co"], subject="Subject!",
                          body_html="<b>hi</b>")
        event_call = [c for c in post.calls if c["url"].endswith("/events")][0]
        body = json.loads(event_call["data"])
        self.assertEqual(body["eventType"], "FileShare.StandardEmail")
        self.assertEqual(body["recipientIds"], ["a@b.co", "c@d.co"])
        self.assertEqual(body["eventModel"]["FileShare"],
                         {"BodyOfEmail": "<b>hi</b>", "SubjectOfEmail": "Subject!"})
        self.assertEqual((body["productType"], body["appType"], body["templateLocale"]),
                         ("ESB", "GIP", "EN-US"))
        self.assertEqual(event_call["headers"]["Content-Type"], "application/json")
        # trailing slash on api_url never doubles
        self.assertEqual(event_call["url"], "https://notifications-api.example/events")

    def test_api_refusal_is_loud(self):
        post = FakePost()
        post.event_responses = [(500, {"message": "boom"})]
        with self.assertRaises(RuntimeError) as ctx:
            send(make_client(post))
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))

    def test_accepted_statuses(self):
        for status in (200, 201, 202):
            with self.subTest(status=status):
                post = FakePost()
                post.event_responses = [(status, {"id": "x"})]
                self.assertEqual(event_id(send(make_client(post))), "x")

    def test_event_id_reads_either_key(self):
        self.assertEqual(event_id({"eventId": "a"}), "a")
        self.assertEqual(event_id({"id": "b"}), "b")
        self.assertIsNone(event_id({}))


class TestPackageRules(unittest.TestCase):
    def test_stdlib_only(self):
        """Copied into pure-code Lambda zips and onto Glue: no boto3, no
        requests, no stack module."""
        allowed = {"__future__", "json", "time", "urllib.error", "urllib.request",
                   "pearson_notifications.client"}
        for path in PACKAGE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name, allowed, f"{path.name}: {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module, allowed, f"{path.name}: {node.module}")


if __name__ == "__main__":
    unittest.main()
