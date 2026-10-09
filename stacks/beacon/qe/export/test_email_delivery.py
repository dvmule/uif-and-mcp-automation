# test_email_delivery.py (rewritten at UIFPE-444)
# The email channel via the Pearson Notifications API: template rendering
# (pure), the OAuth token lifecycle (mint, cache, re-mint on 401), the event
# payload contract, the connection test, and the deliver Lambda's branch —
# including the retired ses_link refusal.

import json
import os
import unittest
from unittest import mock

from beacon_export import email_delivery
from beacon_export.lambdas import deliver

ENV = {
    "BEACON_NOTIFICATIONS_SECRET": "uif/beacon/dev/notifications/api",
    "BEACON_NOTIFICATIONS_API_URL": "https://notifications-api.stg-prsn.com",
    "BEACON_NOTIFICATIONS_TOKEN_URL": "https://pi.example/tokens",
    "BEACON_PANEL_URL": "https://panel.example/m/beacon/index.html",
}


class FakeSecrets:
    def __init__(self):
        self.value = {"client_id": "cid", "client_secret": "cs", "scope": "sc"}
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


def make_adapter(post=None, secrets=None, now=None):
    email_delivery._token_cache.clear()
    clock = now or (lambda: 1000.0)
    return email_delivery.PearsonNotificationsAdapter(
        secrets or FakeSecrets(),
        secret_ref="uif/beacon/dev/notifications/api",
        api_url="https://notifications-api.stg-prsn.com",
        token_url="https://pi.example/tokens",
        panel_url="https://panel.example/m/beacon/index.html",
        post=post or FakePost(), now=clock)


class TestRenderEmail(unittest.TestCase):
    def test_fills_every_token_and_links_to_the_panel(self):
        subject, body = email_delivery.render_email(
            export_name="testexport", job_id="100005", batch_id="b-1",
            file_names=["e_b-1.manifest.json", "e_b-1.csv.zip"],
            download_link="https://panel.example/m/beacon/index.html#/m/beacon/exports/100005")
        self.assertIn("testexport", subject)
        self.assertIn("b-1", subject)
        self.assertIn("testexport (#100005)", body)
        self.assertIn("b-1", body)
        # manifest listed LAST, same guarantee as every channel
        self.assertLess(body.index("e_b-1.csv.zip"), body.index("e_b-1.manifest.json"))
        self.assertIn("#/m/beacon/exports/100005", body)
        self.assertNotIn("${", body)  # no unfilled template tokens

    def test_escapes_author_visible_text(self):
        _, body = email_delivery.render_email(
            export_name="<b>x</b>", job_id="1", batch_id="b",
            file_names=["a<script>.csv"], download_link="https://x")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)


class TestTokenLifecycle(unittest.TestCase):
    def test_mints_once_and_caches(self):
        post = FakePost()
        post.event_responses = [(201, {"eventId": "e1"}), (201, {"eventId": "e2"})]
        adapter = make_adapter(post=post)
        adapter.execute(["f.csv"], recipients=["a@b.co"], job_id="1",
                        export_name="x", batch_id="b1")
        adapter.execute(["f.csv"], recipients=["a@b.co"], job_id="1",
                        export_name="x", batch_id="b2")
        token_calls = [c for c in post.calls if "token" in c["url"]]
        self.assertEqual(len(token_calls), 1)  # cached for the warm Lambda
        payload = json.loads(token_calls[0]["data"])
        # the secret's client_id/client_secret keys carry the PI credentials
        self.assertEqual(payload, {"userName": "cid", "password": "cs"})

    def test_expired_cache_re_mints(self):
        post = FakePost()
        post.token_responses = [(200, {"data": "tok-1", "expires_in": 10}),
                                (200, {"data": "tok-2", "expires_in": 300})]
        post.event_responses = [(201, {"eventId": "e1"}), (201, {"eventId": "e2"})]
        # deterministic clock: first send at t=0, second far past the 10s TTL
        times = [0.0, 0.0, 500.0, 500.0]
        adapter = make_adapter(post=post,
                               now=lambda: times.pop(0) if times else 500.0)
        adapter.execute(["f"], recipients=["a@b.co"], job_id="1",
                        export_name="x", batch_id="b1")
        adapter.execute(["f"], recipients=["a@b.co"], job_id="1",
                        export_name="x", batch_id="b2")
        self.assertEqual(len([c for c in post.calls if "token" in c["url"]]), 2)

    def test_401_re_mints_once_and_retries(self):
        post = FakePost()
        post.token_responses = [(200, {"data": "stale", "expires_in": 300}),
                                (200, {"data": "fresh", "expires_in": 300})]
        post.event_responses = [(401, {}), (201, {"eventId": "e-ok"})]
        adapter = make_adapter(post=post)
        result = adapter.execute(["f"], recipients=["a@b.co"], job_id="1",
                                 export_name="x", batch_id="b1")
        self.assertEqual(result[0]["event_id"], "e-ok")
        events = [c for c in post.calls if c["url"].endswith("/events")]
        self.assertEqual(events[0]["headers"]["X-Authorization"], "stale")
        self.assertEqual(events[1]["headers"]["X-Authorization"], "fresh")

    def test_wrapped_data_shape_tolerated(self):
        # some PI deployments wrap the token: {"data": {"token": "..."}}
        post = FakePost()
        post.token_responses = [(200, {"data": {"token": "wrapped"}})]
        adapter = make_adapter(post=post)
        adapter.execute(["f"], recipients=["a@b.co"], job_id="1",
                        export_name="x", batch_id="b1")
        event_call = [c for c in post.calls if c["url"].endswith("/events")][0]
        self.assertEqual(event_call["headers"]["X-Authorization"], "wrapped")

    def test_token_refusal_is_a_readable_error(self):
        post = FakePost()
        post.token_responses = [(401, {"message": "bad credentials"})]
        adapter = make_adapter(post=post)
        with self.assertRaises(RuntimeError) as ctx:
            adapter.execute(["f"], recipients=["a@b.co"], job_id="1",
                            export_name="x", batch_id="b1")
        self.assertIn("token", str(ctx.exception))


class TestEventContract(unittest.TestCase):
    def test_event_payload_shape(self):
        post = FakePost()
        adapter = make_adapter(post=post)
        result = adapter.execute(
            ["f.csv.zip", "f.manifest.json"], recipients=["p@x.com", "q@y.com"],
            job_id="100005", export_name="testexport", batch_id="b-9")
        event_call = [c for c in post.calls if c["url"].endswith("/events")][0]
        event = json.loads(event_call["data"])
        self.assertEqual(event["productType"], "ESB")
        self.assertEqual(event["appType"], "GIP")
        self.assertEqual(event["eventType"], "FileShare.StandardEmail")
        self.assertEqual(event["recipientIds"], ["p@x.com", "q@y.com"])
        self.assertEqual(event["templateLocale"], "EN-US")
        fs = event["eventModel"]["FileShare"]
        self.assertIn("b-9", fs["SubjectOfEmail"])
        self.assertIn("#/m/beacon/exports/100005", fs["BodyOfEmail"])
        self.assertEqual(result, [{"step": "email", "event_id": "evt-1",
                                   "recipients": ["p@x.com", "q@y.com"],
                                   "files_listed": 2, "ok": True}])

    def test_api_refusal_is_loud(self):
        post = FakePost()
        post.event_responses = [(500, {"message": "boom"})]
        adapter = make_adapter(post=post)
        with self.assertRaises(RuntimeError) as ctx:
            adapter.execute(["f"], recipients=["a@b.co"], job_id="1",
                            export_name="x", batch_id="b")
        self.assertIn("500", str(ctx.exception))

    def test_send_test_reports_instead_of_raising(self):
        post = FakePost()
        post.event_responses = [(403, {"message": "denied"})]
        adapter = make_adapter(post=post)
        result = adapter.send_test(["a@b.co"])
        self.assertFalse(result["ok"])
        self.assertIn("403", result["error"])

        post.event_responses = [(201, {"eventId": "evt-t"})]
        ok = adapter.send_test(["a@b.co"])
        self.assertEqual(ok, {"ok": True, "event_id": "evt-t"})


class TestDeliverLambdaBranch(unittest.TestCase):
    def test_retired_ses_link_refused_with_republish_hint(self):
        with mock.patch.dict(os.environ, ENV):
            with self.assertRaises(NotImplementedError) as ctx:
                deliver._deliver_one(
                    {"channel": "email", "adapter": "ses_link",
                     "recipients": ["a@b.co"]},
                    cfg={"job_id": "1"}, batch_id="b", names=["f"],
                    pickup_prefix="s3://b/p/", pickup_bucket="b",
                    pickup_key_prefix="p/", s3_client=None,
                    secrets_client=FakeSecrets())
        self.assertIn("republish", str(ctx.exception))

    def test_missing_wiring_refuses_honestly(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BEACON_NOTIFICATIONS_SECRET", None)
            with self.assertRaises(RuntimeError) as ctx:
                deliver._email_adapter(None)
        self.assertIn("BEACON_NOTIFICATIONS_SECRET", str(ctx.exception))

    def test_connection_test_email_routes_to_the_adapter(self):
        email_delivery._token_cache.clear()
        with mock.patch.dict(os.environ, ENV), \
             mock.patch.object(email_delivery.PearsonNotificationsAdapter,
                               "send_test", return_value={"ok": True}) as sent:
            result = deliver.handler(
                {"action": "connection_test", "channel": "email",
                 "recipients": ["a@b.co"]},
                secrets_client=FakeSecrets())
        self.assertEqual(result, {"ok": True})
        sent.assert_called_once_with(["a@b.co"])
