# test_destinations_store.py
# UIFPE-241/339/438: destinations as REAL records — persisted in DynamoDB,
# credential written to Secrets Manager at registration (D16: stored nowhere
# else), one Transfer Family connector per SFTP destination, and the
# service-side connection test flow (pin-then-retry on first contact).
# Fake clients, no AWS.

import json
import os
import unittest

from fastapi.testclient import TestClient

from control_panel import registries
from control_panel.api import app

client = TestClient(app)


class FakeDdb:
    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item):
        self.items[(Item["registry"]["S"], Item["id"]["S"])] = Item

    def get_item(self, TableName, Key):
        item = self.items.get((Key["registry"]["S"], Key["id"]["S"]))
        return {"Item": item} if item else {}

    def delete_item(self, TableName, Key):
        self.items.pop((Key["registry"]["S"], Key["id"]["S"]), None)

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues,
              **kwargs):
        registry = ExpressionAttributeValues[":r"]["S"]
        return {"Items": [v for (r, _), v in self.items.items() if r == registry]}


class FakeSecrets:
    # real boto3 exception names — production code matches on type(e).__name__
    class ResourceExistsException(Exception):
        pass

    class ResourceNotFoundException(Exception):
        pass

    _Exists = ResourceExistsException
    _NotFound = ResourceNotFoundException

    def __init__(self):
        self.values = {}
        self.deleted = []
        self.exceptions = type("E", (), {
            "ResourceExistsException": self._Exists,
            "ResourceNotFoundException": self._NotFound})

    @staticmethod
    def _arn(name):
        # the real service appends a random suffix; a fixed one is enough to
        # prove the ARN (not the name) reaches the connector (UIFPE-339)
        return f"arn:aws:secretsmanager:eu-west-1:1:secret:{name}-AbCdEf"

    def create_secret(self, Name, SecretString):
        if Name in self.values:
            raise self._Exists()
        self.values[Name] = SecretString
        return {"ARN": self._arn(Name), "Name": Name}

    def put_secret_value(self, SecretId, SecretString):
        assert SecretId in self.values, "put on a secret that was never created"
        self.values[SecretId] = SecretString
        return {"ARN": self._arn(SecretId), "Name": SecretId}

    def get_secret_value(self, SecretId):
        return {"SecretString": self.values[SecretId]}

    def delete_secret(self, SecretId, RecoveryWindowInDays=None,
                      ForceDeleteWithoutRecovery=None):
        if SecretId not in self.values:
            raise self._NotFound()
        del self.values[SecretId]
        self.deleted.append((SecretId, RecoveryWindowInDays))


class FakeTransfer:
    """UIFPE-339: the connector lifecycle, faked. Real boto3 exception names —
    production code matches on type(e).__name__."""

    class ResourceNotFoundException(Exception):
        pass

    def __init__(self):
        self.connectors = {}
        self.calls = []
        # None = behave like the real service (below); a dict overrides
        self.test_result = None
        self.fail_create = False
        # the "server's" identity — per-test switchable, so multi-destination
        # tests can pin distinct keys
        self.host_key = "ssh-ed25519 QUJD"
        self._n = 0

    def create_connector(self, Url, AccessRole, SftpConfig, Tags):
        self.calls.append(("create", Url, AccessRole, SftpConfig, Tags))
        if self.fail_create:
            raise RuntimeError("boom")
        self._n += 1
        cid = f"c-{self._n:04d}"
        self.connectors[cid] = {"ConnectorId": cid, "Url": Url,
                                "SftpConfig": dict(SftpConfig), "Tags": Tags}
        return {"ConnectorId": cid}

    def test_connection(self, ConnectorId):
        self.calls.append(("test", ConnectorId))
        if self.test_result is not None:
            return {"ConnectorId": ConnectorId, **self.test_result}
        # real service behavior (live finding, UIFPE-339): no TrustedHostKeys
        # = the test REFUSES but hands back the server's host key to pin
        sftp_config = self.connectors[ConnectorId]["SftpConfig"]
        if not sftp_config.get("TrustedHostKeys"):
            return {"ConnectorId": ConnectorId, "Status": "ERROR",
                    "StatusMessage": "No TrustedHostKeys provided.",
                    "SftpConnectionDetails": {"HostKey": self.host_key}}
        return {"ConnectorId": ConnectorId, "Status": "OK",
                "SftpConnectionDetails": {"HostKey": self.host_key}}

    def describe_connector(self, ConnectorId):
        return {"Connector": self.connectors[ConnectorId]}

    def update_connector(self, ConnectorId, **kwargs):
        self.calls.append(("update", ConnectorId, kwargs))
        if "Url" in kwargs:
            self.connectors[ConnectorId]["Url"] = kwargs["Url"]
        if "SftpConfig" in kwargs:
            self.connectors[ConnectorId]["SftpConfig"] = dict(kwargs["SftpConfig"])

    def delete_connector(self, ConnectorId):
        self.calls.append(("delete", ConnectorId))
        if ConnectorId not in self.connectors:
            raise self.ResourceNotFoundException()
        del self.connectors[ConnectorId]


BODY = {"name": "partner-y-sftp", "channel": "sftp", "domain": "product",
        "host": "sftp.partner-y.com", "remote_dir": "/inbound",
        "credential": {"host": "sftp.partner-y.com",
                       "username": "beacon", "password": "pw"}}


class DestinationStoreCase(unittest.TestCase):
    # UIFPE-438: connectors are the only SFTP world — every destination test
    # runs with the transfer fake and the access-role env, like the deployed
    # service.
    def setUp(self):
        from control_panel import transfer_connectors
        registries.reset_destinations()
        self.ddb, self.secrets = FakeDdb(), FakeSecrets()
        self.transfer = FakeTransfer()
        registries.set_destination_clients_for_testing(self.ddb, self.secrets)
        transfer_connectors.set_transfer_client_for_testing(self.transfer)
        os.environ["BEACON_TABLE_REGISTRIES"] = "registries"
        os.environ["BEACON_DESTINATION_SECRET_PREFIX"] = \
            "uif-beacon/test/destinations"
        os.environ["BEACON_TRANSFER_ACCESS_ROLE_ARN"] = \
            "arn:aws:iam::1:role/uif-beacon-test-transfer-access-role"

    def tearDown(self):
        from control_panel import transfer_connectors
        for key in ("BEACON_TABLE_REGISTRIES",
                    "BEACON_DESTINATION_SECRET_PREFIX",
                    "BEACON_TRANSFER_ACCESS_ROLE_ARN"):
            os.environ.pop(key, None)
        registries.set_destination_clients_for_testing(None, None)
        transfer_connectors.set_transfer_client_for_testing(None)


class TestPersistence(DestinationStoreCase):
    def test_create_writes_secret_and_persists_record(self):
        response = client.post("/destinations", json=BODY)
        self.assertEqual(response.status_code, 201)
        record = response.json()
        # UIFPE-606: keyed by the destination's id, never its name
        ref = f"uif-beacon/test/destinations/{record['id']}"
        self.assertEqual(record["secret_ref"], ref)
        # the credential went to Secrets Manager in the connector's shape
        # (capitalized keys; lowercase duplication retired at UIFPE-438),
        # host/port riding along for humans reading the secret
        secret = json.loads(self.secrets.values[ref])
        self.assertEqual(secret, {"host": "sftp.partner-y.com", "port": 22,
                                  "Username": "beacon", "Password": "pw"})
        # ... and NOWHERE else: not in the response, not in the stored row
        self.assertNotIn("password", str(record))
        stored = self.ddb.items[("destinations", record["id"])]
        self.assertNotIn("pw", stored["record"]["S"])
        # the row is really in DynamoDB (a cold start would keep it)
        self.assertEqual(client.get("/destinations").json()[0]["id"],
                         record["id"])

    def test_a_taken_name_is_refused_and_overwrites_nothing(self):
        # UIFPE-606: this used to be "re-registering a name rotates the
        # secret" — which let a SECOND destination with the same name
        # silently replace the first one's password
        first = client.post("/destinations", json=BODY).json()
        for name in ("partner-y-sftp", "  Partner-Y-SFTP "):  # trimmed, any case
            body2 = dict(BODY, name=name, credential={
                "host": BODY["host"], "username": "beacon", "password": "NEW"})
            response = client.post("/destinations", json=body2)
            self.assertEqual(response.status_code, 422, name)
            self.assertIn("already exists", response.json()["detail"])
        self.assertEqual(
            json.loads(self.secrets.values[first["secret_ref"]])["Password"], "pw")
        self.assertEqual(len(client.get("/destinations").json()), 1)

    def test_names_are_unique_across_channels_and_visibility(self):
        # a private row still owns its name — another author cannot take it
        client.post("/destinations", json=dict(BODY, visibility="private"))
        response = client.post("/destinations", json={
            "name": "partner-y-sftp", "channel": "s3",
            "destination_s3_prefix": "s3://bucket/in"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("partner-y-sftp", response.json()["detail"])

    def test_the_stored_name_is_trimmed_and_blank_is_refused(self):
        record = client.post("/destinations",
                             json=dict(BODY, name="  partner-z  ")).json()
        self.assertEqual(record["name"], "partner-z")
        response = client.post("/destinations", json=dict(BODY, name="   "))
        self.assertEqual(response.status_code, 422)

    def test_an_existing_secret_at_the_new_path_is_never_overwritten(self):
        # defence in depth: an id repeating is not expected, but if a secret
        # already sits at the path the create REFUSES rather than put over it
        import uuid
        real = uuid.uuid4

        class _Fixed:
            hex = "deadbeef" + "0" * 24

        uuid.uuid4 = lambda: _Fixed()
        try:
            ref = "uif-beacon/test/destinations/dest-deadbeef"
            self.secrets.values[ref] = json.dumps({"Password": "someone-else"})
            response = client.post("/destinations", json=BODY)
        finally:
            uuid.uuid4 = real
        self.assertEqual(response.status_code, 422)
        self.assertIn("nothing was overwritten", response.json()["detail"])
        self.assertEqual(json.loads(self.secrets.values[ref])["Password"],
                         "someone-else")

    def test_credential_username_and_password_required(self):
        for cred in ({}, {"username": "beacon"}, {"password": "pw"}):
            response = client.post("/destinations",
                                   json=dict(BODY, credential=cred))
            self.assertEqual(response.status_code, 422, cred)


class TestConnectionTest(DestinationStoreCase):
    def _create(self):
        return client.post("/destinations", json=BODY).json()

    def test_first_contact_pins_host_key_on_the_record(self):
        # UIFPE-259/339: the pinned key lives on the DESTINATION RECORD (and
        # in the connector's TrustedHostKeys) — the secret stays
        # credential-only (Keeper sync overwrites secrets one-way from the
        # vault, so a key stored there would be wiped)
        dest = self._create()
        response = client.post(f"/destinations/{dest['id']}/test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(),
                         {"ok": True, "status": "verified",
                          "host_key_pinned": True})
        # the secret was NOT touched — credential only, exactly as created
        secret = json.loads(self.secrets.values[dest["secret_ref"]])
        self.assertNotIn("host_key", secret)
        self.assertEqual(secret["Password"], "pw")
        # the captured key was pinned onto the persisted record
        stored = registries.get_destination(dest["id"])
        self.assertEqual(stored["status"], "verified")
        self.assertEqual(stored["host_key"], "ssh-ed25519 QUJD")
        self.assertEqual(stored["host_key_type"], "ssh-ed25519")

    def test_retest_never_repins(self):
        dest = self._create()
        client.post(f"/destinations/{dest['id']}/test")   # pins on first contact
        self.transfer.host_key = "ssh-ed25519 ROTATED"    # server changed identity
        response = client.post(f"/destinations/{dest['id']}/test")
        # the fake still reports OK, but no re-pin happens — the connector's
        # TrustedHostKeys keeps enforcing the ORIGINAL identity
        self.assertEqual(response.json()["host_key_pinned"], False)
        self.assertEqual(registries.get_destination(dest["id"])["host_key"],
                         "ssh-ed25519 QUJD")

    def test_publish_embeds_the_pinned_key_into_the_delivery_block(self):
        # UIFPE-259: the published config carries the host key (public
        # fingerprint, immutable per version); UIFPE-339 adds the adapter +
        # connector id, resolved from the record server-side
        dest = self._create()
        client.post(f"/destinations/{dest['id']}/test")
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        self.addCleanup(os.environ.pop, "BEACON_DATA_BUCKET", None)
        from control_panel import publisher
        version = {"job_id": "100999", "version": 1, "name": "sftp-feed",
                   "mapping_config": {}, "settings": {
                       "formats": ["csv"],
                       "destination_id": dest["id"],
                       "delivery": {"channel": "sftp",
                                    "host": dest["host"], "port": 22,
                                    "remote_dir": "/inbound",
                                    "secret_ref": dest["secret_ref"]}}}
        cfg, error = publisher.build_export_config(
            version, mapping_uri="s3://demo-bucket/configs/100999/v1/mapping.json")
        self.assertIsNone(error)
        self.assertEqual(cfg["delivery"]["host_key"], "ssh-ed25519 QUJD")
        self.assertEqual(cfg["delivery"]["adapter"], "transfer_family")
        self.assertEqual(cfg["delivery"]["connector_id"], dest["connector_id"])

    def test_publish_embeds_each_targets_own_key_in_a_multi_target_config(self):
        # UIFPE-170: every sftp target pins ITS server's key — two targets,
        # two different pinned keys, each landing on its own delivery block
        dest_a = self._create()
        client.post(f"/destinations/{dest_a['id']}/test")  # pins QUJD
        self.transfer.host_key = "ssh-rsa WFla"
        dest_b = client.post("/destinations", json=dict(
            BODY, name="partner-z-sftp", host="sftp.partner-z.com",
            credential={"host": "sftp.partner-z.com", "username": "beacon",
                        "password": "pw2"})).json()
        client.post(f"/destinations/{dest_b['id']}/test")  # pins WFla

        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        self.addCleanup(os.environ.pop, "BEACON_DATA_BUCKET", None)
        from control_panel import publisher
        version = {"job_id": "100998", "version": 1, "name": "fanout-feed",
                   "mapping_config": {}, "settings": {
                       "formats": ["csv"],
                       "deliveries": [
                           {"channel": "sftp",
                            "destination_id": dest_a["id"],
                            "remote_dir": "/inbound",
                            "secret_ref": dest_a["secret_ref"]},
                           {"channel": "sftp",
                            "destination_id": dest_b["id"],
                            "remote_dir": "/inbound",
                            "secret_ref": dest_b["secret_ref"]}]}}
        cfg, error = publisher.build_export_config(
            version, mapping_uri="s3://demo-bucket/configs/100998/v1/mapping.json")
        self.assertIsNone(error)
        self.assertNotIn("delivery", cfg)
        self.assertEqual([t["host_key"] for t in cfg["deliveries"]],
                         ["ssh-ed25519 QUJD", "ssh-rsa WFla"])
        self.assertEqual([t["connector_id"] for t in cfg["deliveries"]],
                         [dest_a["connector_id"], dest_b["connector_id"]])

    def test_failed_test_reports_error_and_keeps_status(self):
        dest = self._create()
        self.transfer.test_result = {"Status": "ERROR",
                                     "StatusMessage": "Connection refused"}
        response = client.post(f"/destinations/{dest['id']}/test")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])
        self.assertIn("refused", response.json()["error"])
        self.assertEqual(registries.get_destination(dest["id"])["status"],
                         "pending_connection_test")

    def test_unknown_destination_404(self):
        self.assertEqual(client.post("/destinations/dest-nope/test").status_code,
                         404)

    def test_non_testable_channel_422(self):
        # UIFPE-440/444 gave s3 and email real connection tests — only a
        # channel with no adapter at all (planned ones) stays untestable
        registries._put_destination(
            {"id": "dest-gcs", "name": "GCS", "channel": "gcs"})
        response = client.post("/destinations/dest-gcs/test")
        self.assertEqual(response.status_code, 422)

    def test_s3_destination_without_deliver_wiring_503(self):
        # UIFPE-440: the s3 test runs inside the deliver Lambda; a deployment
        # without that wiring says so instead of pretending to test
        record = client.post("/destinations", json={
            "name": "bucket-drop", "channel": "s3", "domain": "product",
            "destination_s3_prefix": "s3://p/in"}).json()
        response = client.post(f"/destinations/{record['id']}/test")
        self.assertEqual(response.status_code, 503)
        self.assertIn("BEACON_DELIVER_FUNCTION", response.json()["detail"])

    def test_pre_connector_destination_refused_409(self):
        # UIFPE-438: a record from before UIFPE-339 has no connector — its
        # Paramiko tester is gone, so the API says re-register, honestly
        registries._destinations.append(
            {"id": "dest-legacy", "name": "OLD", "channel": "sftp",
             "host": "h", "remote_dir": "/in", "secret_ref": "r"})
        registries._put_destination(registries._destinations[-1])
        response = client.post("/destinations/dest-legacy/test")
        self.assertEqual(response.status_code, 409)
        self.assertIn("re", response.json()["detail"].lower())


class TestUpdateDestination(DestinationStoreCase):
    """UIFPE-337: edit — channel immutable, name editable and unique
    (UIFPE-606), sftp edits unproven until
    re-tested, credential rotation replaces the secret, host moves clear
    the pinned key."""

    def _create_verified(self):
        dest = client.post("/destinations", json=BODY).json()
        client.post(f"/destinations/{dest['id']}/test")  # pins QUJD, verifies
        return client.get("/destinations").json()[0]

    def test_channel_is_immutable(self):
        dest = self._create_verified()
        response = client.put(f"/destinations/{dest['id']}", json={"channel": "s3"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("immutable", response.json()["detail"])

    def test_rename_keeps_the_secret_connector_pin_and_status(self):
        # UIFPE-606: the name is a label — the credential stays where it was
        # created, the connector and pinned key are untouched, and a rename
        # alone re-opens no connection test
        dest = self._create_verified()
        before = dict(self.secrets.values)
        response = client.put(f"/destinations/{dest['id']}",
                              json={"name": "  Partner Y — renamed ",
                                    "host": dest["host"],
                                    "remote_dir": dest["remote_dir"]})
        self.assertEqual(response.status_code, 200)
        stored = registries.get_destination(dest["id"])
        self.assertEqual(stored["name"], "Partner Y — renamed")
        self.assertEqual(stored["secret_ref"], dest["secret_ref"])
        self.assertEqual(stored["connector_id"], dest["connector_id"])
        self.assertEqual(stored["host_key"], "ssh-ed25519 QUJD")
        self.assertEqual(stored["status"], "verified")
        self.assertEqual(self.secrets.values, before)
        # ... and it still tests and rotates through the SAME secret
        self.assertEqual(client.post(f"/destinations/{dest['id']}/test")
                         .json()["ok"], True)
        client.put(f"/destinations/{dest['id']}", json={
            "credential": {"username": "beacon", "password": "ROTATED"}})
        self.assertEqual(json.loads(
            self.secrets.values[dest["secret_ref"]])["Password"], "ROTATED")

    def test_rename_to_a_taken_name_is_refused_and_changes_nothing(self):
        dest = self._create_verified()
        client.post("/destinations", json={
            "name": "partner-s3", "channel": "s3",
            "destination_s3_prefix": "s3://bucket/in"})
        response = client.put(f"/destinations/{dest['id']}",
                              json={"name": "PARTNER-S3", "remote_dir": "/moved"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("already exists", response.json()["detail"])
        stored = registries.get_destination(dest["id"])
        self.assertEqual(stored["name"], "partner-y-sftp")
        self.assertEqual(stored["remote_dir"], "/inbound")

    def test_own_name_and_case_change_are_not_a_clash(self):
        dest = self._create_verified()
        for name in ("partner-y-sftp", "Partner-Y-SFTP"):
            response = client.put(f"/destinations/{dest['id']}", json={"name": name})
            self.assertEqual(response.status_code, 200, name)
        self.assertEqual(registries.get_destination(dest["id"])["name"],
                         "Partner-Y-SFTP")

    def test_blank_rename_is_refused(self):
        dest = self._create_verified()
        response = client.put(f"/destinations/{dest['id']}", json={"name": "  "})
        self.assertEqual(response.status_code, 422)
        self.assertIn("blank", response.json()["detail"])

    def test_a_legacy_duplicate_pair_can_still_edit_other_fields(self):
        # rows created before names were unique may share one; the check runs
        # only when the name CHANGES, so neither is locked out of edits
        registries._put_destination({"id": "dest-old1", "name": "dup",
                                     "channel": "s3", "status": "verified",
                                     "destination_s3_prefix": "s3://a/in"})
        registries._put_destination({"id": "dest-old2", "name": "dup",
                                     "channel": "s3", "status": "verified",
                                     "destination_s3_prefix": "s3://b/in"})
        response = client.put("/destinations/dest-old2", json={
            "name": "dup", "destination_s3_prefix": "s3://b/moved"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(registries.get_destination("dest-old2")
                         ["destination_s3_prefix"], "s3://b/moved")

    def test_a_legacy_name_keyed_secret_keeps_working_after_rename(self):
        # destinations created before UIFPE-606 carry a NAME-keyed secret_ref;
        # every reader uses the stored value, so a rename cannot orphan it
        legacy_ref = "uif-beacon/test/destinations/partner-legacy"
        self.secrets.values[legacy_ref] = json.dumps(
            {"host": "h", "port": 22, "Username": "u", "Password": "old"})
        registries._put_destination({
            "id": "dest-leg", "name": "partner-legacy", "channel": "sftp",
            "status": "verified", "host": "h", "port": 22,
            "remote_dir": "/in", "secret_ref": legacy_ref})
        client.put("/destinations/dest-leg", json={"name": "partner-renamed"})
        client.put("/destinations/dest-leg", json={
            "credential": {"username": "u", "password": "new"}})
        self.assertEqual(registries.get_destination("dest-leg")["secret_ref"],
                         legacy_ref)
        self.assertEqual(json.loads(self.secrets.values[legacy_ref])["Password"],
                         "new")

    def test_credential_rotation_replaces_secret_and_requires_retest(self):
        dest = self._create_verified()
        response = client.put(f"/destinations/{dest['id']}", json={
            "credential": {"username": "beacon", "password": "ROTATED"}})
        self.assertEqual(response.status_code, 200)
        secret = json.loads(self.secrets.values[dest["secret_ref"]])
        self.assertEqual(secret["Password"], "ROTATED")
        stored = registries.get_destination(dest["id"])
        self.assertEqual(stored["status"], "pending_connection_test")
        # same server: the pinned key SURVIVES a credential rotation
        self.assertEqual(stored["host_key"], "ssh-ed25519 QUJD")

    def test_host_change_clears_the_pin_and_updates_the_secret(self):
        dest = self._create_verified()
        response = client.put(f"/destinations/{dest['id']}", json={
            "host": "sftp2.partner-y.com"})
        self.assertEqual(response.status_code, 200)
        stored = registries.get_destination(dest["id"])
        self.assertEqual(stored["host"], "sftp2.partner-y.com")
        self.assertEqual(stored["status"], "pending_connection_test")
        self.assertNotIn("host_key", stored)  # old server's identity, gone
        secret = json.loads(self.secrets.values[dest["secret_ref"]])
        self.assertEqual(secret["host"], "sftp2.partner-y.com")
        self.assertEqual(secret["Password"], "pw")  # credential kept

    def test_remote_dir_edit_keeps_the_pin_but_requires_retest(self):
        dest = self._create_verified()
        client.put(f"/destinations/{dest['id']}", json={"remote_dir": "/drop"})
        stored = registries.get_destination(dest["id"])
        self.assertEqual(stored["remote_dir"], "/drop")
        self.assertEqual(stored["host_key"], "ssh-ed25519 QUJD")  # same server
        self.assertEqual(stored["status"], "pending_connection_test")

    def test_unknown_destination_404(self):
        self.assertEqual(
            client.put("/destinations/dest-nope", json={}).status_code, 404)


class TestEmailChannelEnabled(unittest.TestCase):
    def test_new_email_destination_registers_pending(self):
        # UIFPE-444: the channel is back, on the Pearson Notifications API —
        # no credential from the author, pending until its test mail sends
        registries.reset_destinations()
        response = client.post("/destinations", json={
            "name": "mail-out", "channel": "email", "domain": "product",
            "recipients": ["partner@example.com"]})
        self.assertEqual(response.status_code, 201)
        record = response.json()
        self.assertEqual(record["status"], "pending_connection_test")
        self.assertEqual(record["recipients"], ["partner@example.com"])
        self.assertNotIn("secret_ref", record)


class TestDeleteDestination(DestinationStoreCase):
    """UIFPE-337: delete removes the record AND (sftp) the secret — with the
    in-use guard and the never-orphan-a-credential ordering."""

    def _create(self, body=BODY):
        return client.post("/destinations", json=body).json()

    def test_delete_unused_sftp_removes_row_and_schedules_secret_deletion(self):
        dest = self._create()
        response = client.delete(f"/destinations/{dest['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(),
                         {"deleted": True, "id": dest["id"],
                          "secret_scheduled_for_deletion": True})
        self.assertEqual(self.secrets.deleted, [(dest["secret_ref"], 7)])
        self.assertIsNone(registries.get_destination(dest["id"]))
        self.assertEqual(client.get("/destinations").json(), [])

    def test_delete_s3_destination_touches_no_secret(self):
        dest = self._create({"name": "bucket-drop", "channel": "s3",
                             "domain": "product",
                             "destination_s3_prefix": "s3://p/in"})
        response = client.delete(f"/destinations/{dest['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["secret_scheduled_for_deletion"])
        self.assertEqual(self.secrets.deleted, [])

    def test_delete_refused_while_a_published_export_uses_it(self):
        from unittest import mock
        from control_panel import lifecycle
        dest = self._create()
        latest = [{"job_id": "100777", "name": "acme-feed",
                   "settings": {"destination_id": dest["id"]}}]
        with mock.patch.object(lifecycle, "catalog", return_value=latest):
            response = client.delete(f"/destinations/{dest['id']}")
        self.assertEqual(response.status_code, 409)
        self.assertIn("acme-feed", response.json()["detail"])
        # nothing was touched
        self.assertIsNotNone(registries.get_destination(dest["id"]))
        self.assertEqual(self.secrets.deleted, [])

    def test_secret_ref_match_also_guards(self):
        # an export published before destination_id tracking still blocks
        from unittest import mock
        from control_panel import lifecycle
        dest = self._create()
        latest = [{"job_id": "100778", "name": "legacy-feed", "settings": {
            "delivery": {"channel": "sftp", "secret_ref": dest["secret_ref"]}}}]
        with mock.patch.object(lifecycle, "catalog", return_value=latest):
            self.assertEqual(
                client.delete(f"/destinations/{dest['id']}").status_code, 409)

    def test_destination_inside_a_multi_target_list_also_guards(self):
        # UIFPE-170: a multi-target export carries its destinations in
        # settings.deliveries — the guard must find this destination there
        from unittest import mock
        from control_panel import lifecycle
        dest = self._create()
        latest = [{"job_id": "100779", "name": "fanout-feed", "settings": {
            "deliveries": [
                {"channel": "s3", "destination_s3_prefix": "s3://a/in"},
                {"channel": "sftp", "destination_id": dest["id"],
                 "secret_ref": dest["secret_ref"], "remote_dir": "/in"}]}}]
        with mock.patch.object(lifecycle, "catalog", return_value=latest):
            response = client.delete(f"/destinations/{dest['id']}")
        self.assertEqual(response.status_code, 409)
        self.assertIn("fanout-feed", response.json()["detail"])
        # a multi-target export NOT delivering here never blocks
        other = [{"job_id": "100780", "name": "other-feed", "settings": {
            "deliveries": [{"channel": "s3",
                            "destination_s3_prefix": "s3://b/in"}]}}]
        with mock.patch.object(lifecycle, "catalog", return_value=other):
            self.assertEqual(
                client.delete(f"/destinations/{dest['id']}").status_code, 200)

    def test_secret_delete_failure_keeps_the_record(self):
        # the one forbidden outcome is an orphaned credential: if the secret
        # cannot be deleted, the row stays so the secret remains visible
        dest = self._create()

        def boom(SecretId, RecoveryWindowInDays=None):
            raise RuntimeError("access denied")
        self.secrets.delete_secret = boom
        response = client.delete(f"/destinations/{dest['id']}")
        self.assertEqual(response.status_code, 502)
        self.assertIn("destination kept", response.json()["detail"])
        self.assertIsNotNone(registries.get_destination(dest["id"]))

    def test_already_scheduled_secret_does_not_block_row_deletion(self):
        # retry after a partial failure: the secret is scheduled for deletion
        # (InvalidRequestException on a second delete) — the row must still go
        dest = self._create()

        class _Invalid(Exception):
            pass
        _Invalid.__name__ = "InvalidRequestException"

        def already_scheduled(SecretId, RecoveryWindowInDays=None):
            raise _Invalid("already scheduled for deletion")
        self.secrets.delete_secret = already_scheduled
        response = client.delete(f"/destinations/{dest['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["secret_scheduled_for_deletion"])
        self.assertIsNone(registries.get_destination(dest["id"]))

    def test_already_missing_secret_does_not_block_row_deletion(self):
        dest = self._create()
        del self.secrets.values[dest["secret_ref"]]  # admin removed it already
        response = client.delete(f"/destinations/{dest['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["secret_scheduled_for_deletion"])
        self.assertIsNone(registries.get_destination(dest["id"]))

    def test_unknown_destination_404(self):
        self.assertEqual(client.delete("/destinations/dest-nope").status_code,
                         404)


class TransferFamilyCase(DestinationStoreCase):
    """UIFPE-339: with the access-role env set, new SFTP destinations ride
    Transfer Family connectors — created with the secret, tested through the
    service, repointed on edit, deleted with the record."""

    # UIFPE-438: the base case is connector-only now — this subclass keeps
    # its name for the lifecycle tests below and adds only the helper.
    ROLE = "arn:aws:iam::1:role/uif-beacon-test-transfer-access-role"

    def _create(self, **overrides):
        return client.post("/destinations", json={**BODY, **overrides}).json()


class TestConnectorLifecycle(TransferFamilyCase):
    def test_register_creates_a_tagged_connector(self):
        record = self._create()
        self.assertEqual(record["adapter"], "transfer_family")
        self.assertEqual(record["connector_id"], "c-0001")
        kind, url, role, sftp_config, tags = self.transfer.calls[0]
        self.assertEqual((kind, url, role),
                         ("create", "sftp://sftp.partner-y.com:22", self.ROLE))
        # the secret is the connector's credential source (D16 unchanged) —
        # by its full ARN, the only form CreateConnector accepts — and NO
        # trusted host keys yet: pinned by the first connection test
        self.assertEqual(sftp_config,
                         {"UserSecretId": FakeSecrets._arn(record["secret_ref"])})
        self.assertEqual(record["secret_arn"],
                         FakeSecrets._arn(record["secret_ref"]))
        # mandatory Project tag: untagged = unreachable under the tag fence
        self.assertEqual(tags, [{"Key": "Project", "Value": "beacon"}])

    def test_connector_failure_creates_nothing_and_orphans_no_secret(self):
        self.transfer.fail_create = True
        response = client.post("/destinations", json=BODY)
        self.assertEqual(response.status_code, 422)
        self.assertIn("connector", response.json()["detail"])
        self.assertEqual(client.get("/destinations").json(), [])
        # nothing left behind under the destinations prefix at all
        self.assertEqual(self.secrets.values, {})

    def test_service_test_pins_host_key_on_record_and_connector(self):
        record = self._create()
        response = client.post(f"/destinations/{record['id']}/test")
        self.assertEqual(response.json(), {"ok": True, "status": "verified",
                                           "host_key_pinned": True})
        stored = registries.get_destination(record["id"])
        self.assertEqual(stored["host_key"], "ssh-ed25519 QUJD")
        self.assertEqual(stored["host_key_type"], "ssh-ed25519")
        # ... and the connector now REFUSES any other server identity
        sftp_config = self.transfer.connectors["c-0001"]["SftpConfig"]
        self.assertEqual(sftp_config["TrustedHostKeys"], ["ssh-ed25519 QUJD"])
        self.assertEqual(sftp_config["UserSecretId"],
                         FakeSecrets._arn(record["secret_ref"]))

    def test_failed_service_test_reports_and_stays_pending(self):
        self.transfer.test_result = {"Status": "ERROR",
                                     "StatusMessage": "auth failed"}
        record = self._create()
        response = client.post(f"/destinations/{record['id']}/test")
        self.assertEqual(response.json()["ok"], False)
        self.assertEqual(response.json()["error"], "auth failed")
        self.assertEqual(registries.get_destination(record["id"])["status"],
                         "pending_connection_test")

    def test_host_change_repoints_connector_and_clears_trusted_keys(self):
        record = self._create()
        client.post(f"/destinations/{record['id']}/test")  # pins the key
        response = client.put(f"/destinations/{record['id']}",
                              json={"host": "sftp.moved.example"})
        self.assertEqual(response.status_code, 200)
        connector = self.transfer.connectors["c-0001"]
        self.assertEqual(connector["Url"], "sftp://sftp.moved.example:22")
        # the pin belonged to the OLD server — cleared on both sides
        self.assertNotIn("TrustedHostKeys", connector["SftpConfig"])
        stored = registries.get_destination(record["id"])
        self.assertNotIn("host_key", stored)
        self.assertEqual(stored["status"], "pending_connection_test")

    def test_delete_removes_connector_before_secret(self):
        record = self._create()
        response = client.delete(f"/destinations/{record['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.transfer.connectors, {})
        self.assertEqual(self.secrets.deleted[0][0], record["secret_ref"])

    def test_publish_embeds_connector_id_and_adapter(self):
        # the published config resolves the adapter from the RECORD, never
        # from the client payload — a connector destination publishes the
        # transfer_family shape, so the deliver Lambda picks the right path
        record = self._create()
        client.post(f"/destinations/{record['id']}/test")
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        self.addCleanup(os.environ.pop, "BEACON_DATA_BUCKET", None)
        from control_panel import publisher
        version = {"job_id": "100997", "version": 1, "name": "tf-feed",
                   "mapping_config": {}, "settings": {
                       "formats": ["csv"],
                       "destination_id": record["id"],
                       "delivery": {"channel": "sftp",
                                    "host": record["host"], "port": 22,
                                    "remote_dir": "/inbound",
                                    "secret_ref": record["secret_ref"]}}}
        cfg, error = publisher.build_export_config(
            version, mapping_uri="s3://demo-bucket/configs/100997/v1/mapping.json")
        self.assertIsNone(error)
        self.assertEqual(cfg["delivery"]["adapter"], "transfer_family")
        self.assertEqual(cfg["delivery"]["connector_id"], "c-0001")
        self.assertEqual(cfg["delivery"]["host_key"], "ssh-ed25519 QUJD")


if __name__ == "__main__":
    unittest.main()
