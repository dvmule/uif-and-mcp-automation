# test_partner_schema.py (UIFPE-558)
# The partner's XSD as part of an export: safe parsing of the supported core
# (with everything beyond it reported, not ignored), the tree/paths the
# wizard's guide renders, the required-unbound check, staging (local and S3),
# the publish-time freeze into the immutable version folder, and the gate
# that refuses to publish while required elements are unbound.

import io
import json
import os
import unittest

from control_panel import lifecycle, partner_schema, publisher
from lifecycle_fixtures import MAPPING, client, make_draft

XSD_HEAD = '<?xml version="1.0"?><xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'

SIMPLE = XSD_HEAD + """
<xs:element name="Product">
  <xs:complexType>
    <xs:sequence>
      <xs:element name="productId" type="xs:string"/>
      <xs:element name="Title" minOccurs="0">
        <xs:complexType>
          <xs:simpleContent><xs:extension base="xs:string">
            <xs:attribute name="lang" use="required"/>
          </xs:extension></xs:simpleContent>
        </xs:complexType>
      </xs:element>
      <xs:element name="Identifiers">
        <xs:complexType><xs:sequence>
          <xs:element name="ISBN13" type="xs:string"/>
          <xs:element name="ISBN10" type="xs:string" minOccurs="0"/>
        </xs:sequence></xs:complexType>
      </xs:element>
    </xs:sequence>
  </xs:complexType>
</xs:element>
</xs:schema>"""

WRAPPED = XSD_HEAD + """
<xs:element name="Products">
  <xs:complexType><xs:sequence>
    <xs:element name="Product" maxOccurs="unbounded">
      <xs:complexType><xs:sequence>
        <xs:element name="productId" type="xs:string"/>
      </xs:sequence></xs:complexType>
    </xs:element>
  </xs:sequence></xs:complexType>
</xs:element>
</xs:schema>"""

DEMANDING = XSD_HEAD + """
<xs:element name="Product">
  <xs:complexType><xs:sequence>
    <xs:element name="productId" type="xs:string"/>
    <xs:element name="Isbn" type="xs:string"/>
  </xs:sequence></xs:complexType>
</xs:element>
</xs:schema>"""


class TestParse(unittest.TestCase):
    def test_core_shapes_parse(self):
        schema, error = partner_schema.parse_xsd(SIMPLE)
        self.assertIsNone(error)
        paths = partner_schema.schema_paths(schema)
        self.assertEqual(paths["row_tag"], "Product")
        by_path = {p["path"]: p for p in paths["paths"]}
        self.assertTrue(by_path["productId"]["required"])
        self.assertTrue(by_path["Identifiers/ISBN13"]["required"])
        self.assertFalse(by_path["Identifiers/ISBN10"]["required"])
        # Title is optional, so its required attribute doesn't bind either
        self.assertFalse(by_path["Title@lang"]["required"])
        self.assertFalse(by_path["Title"]["required"])

    def test_wrapper_with_repeating_child_names_the_record(self):
        schema, error = partner_schema.parse_xsd(WRAPPED)
        self.assertIsNone(error)
        paths = partner_schema.schema_paths(schema)
        self.assertEqual(paths["root_tag"], "Products")
        self.assertEqual(paths["row_tag"], "Product")
        self.assertEqual([p["path"] for p in paths["paths"]], ["productId"])

    def test_refusals_are_readable(self):
        cases = [
            ("", "empty"),
            ("<not-xml", "well-formed"),
            ("<root/>", "xs:schema"),
            (XSD_HEAD + "</xs:schema>", "no top-level element"),
            ("<!DOCTYPE x []>" + SIMPLE, "DOCTYPE"),
        ]
        for text, fragment in cases:
            schema, error = partner_schema.parse_xsd(text)
            self.assertIsNone(schema)
            self.assertIn(fragment, error)

    def test_sample_document_is_named_as_such(self):
        _, error = partner_schema.parse_xsd("<Products><Product/></Products>")
        self.assertIn("sample document", error)

    def test_unsupported_constructs_are_reported_not_ignored(self):
        text = XSD_HEAD + ('<xs:import namespace="x" schemaLocation="y"/>'
                           '<xs:element name="P" type="xs:string"/></xs:schema>')
        schema, error = partner_schema.parse_xsd(text)
        self.assertIsNone(error)
        self.assertTrue(any("xs:import" in u for u in schema["unsupported"]))

    def test_declared_covers_inner_elements_not_only_leaves(self):
        """UIFPE-587: binding a structured column to an inner element is
        legitimate (it writes that column's fields as children), so
        `Identifiers` counts as declared even though only its leaves are
        bindable."""
        schema, _ = partner_schema.parse_xsd(SIMPLE)
        declared = partner_schema.declared_paths(schema)
        self.assertIn("Identifiers", declared)
        self.assertIn("Identifiers/ISBN13", declared)
        self.assertIn("Title@lang", declared)
        # the bindable list stays leaves + attributes only
        paths = {p["path"] for p in partner_schema.schema_paths(schema)["paths"]}
        self.assertNotIn("Identifiers", paths)
        # and the API/UI get the wider set alongside it
        self.assertIn("Identifiers", partner_schema.schema_paths(schema)["declared"])

    def test_unknown_targets_names_what_the_schema_never_declares(self):
        schema, _ = partner_schema.parse_xsd(SIMPLE)
        unknown = partner_schema.unknown_targets(schema, [
            "productId",              # declared leaf
            "Identifiers",            # declared inner element
            "Title@lang",             # declared attribute
            "organization_code",      # not in the schema
            "Contributors/Contributor",  # not in the schema
            "Title@dir",              # undeclared ATTRIBUTE on a declared element
            "organization_code",      # repeated — reported once
        ])
        self.assertEqual(unknown, ["organization_code",
                                   "Contributors/Contributor", "Title@dir"])

    def test_unknown_targets_is_empty_when_everything_is_declared(self):
        schema, _ = partner_schema.parse_xsd(SIMPLE)
        self.assertEqual(partner_schema.unknown_targets(
            schema, ["productId", "Identifiers/ISBN10"]), [])

    def test_missing_required_lists_only_unbound_required(self):
        schema, _ = partner_schema.parse_xsd(SIMPLE)
        missing = partner_schema.missing_required(
            schema, ["productId"])
        self.assertEqual(missing, ["Identifiers/ISBN13"])
        self.assertEqual(partner_schema.missing_required(
            schema, ["productId", "Identifiers/ISBN13"]), [])


SAMPLE = """<?xml version="1.0"?>
<Products xmlns="urn:partner:catalog">
  <Product id="1"><!-- first -->
    <Title lang="en">Campbell Biology</Title>
    <Identifiers><ISBN13>9780135437469</ISBN13><ISBN10>0135437460</ISBN10></Identifiers>
    <Price>54.99</Price>
    <Published>2026-01-15</Published>
    <Author>Jane</Author><Author>Lee</Author>
  </Product>
  <Product id="2">
    <Identifiers><ISBN13>9780135437391</ISBN13></Identifiers>
    <Price>41</Price>
    <Published>2025-09-01</Published>
    <Author>Sam</Author>
  </Product>
</Products>"""


class TestInferFromSample(unittest.TestCase):
    """UIFPE-660: a sample document becomes the loosest XSD that fits it."""

    def test_sample_becomes_a_schema_the_parser_accepts(self):
        xsd, info, error = partner_schema.infer_xsd_from_xml(SAMPLE)
        self.assertIsNone(error)
        notes = info["notes"]
        # the drawer's facts and tree
        self.assertEqual((info["wrapper"], info["record"], info["records"]),
                         ("Products", "Product", 2))
        rows = {r["path"]: r for r in info["rows"]}
        self.assertEqual(rows["@id"], {"path": "@id", "kind": "attribute", "required": True,
                                       "repeats": False, "type": "string"})
        self.assertEqual(rows["Author"]["repeats"], True)
        self.assertEqual(rows["Price"]["type"], "decimal")
        self.assertFalse(rows["Title@lang"]["required"])  # Title itself is optional
        # bindable leaves only — Identifiers is an inner element, not a row
        self.assertEqual((info["elements"], info["attributes"]), (6, 2))
        schema, error = partner_schema.parse_xsd(xsd)
        self.assertIsNone(error, error)
        paths = partner_schema.schema_paths(schema)
        self.assertEqual((paths["root_tag"], paths["row_tag"]), ("Products", "Product"))
        by_path = {p["path"]: p for p in paths["paths"]}
        # on every record → required; on some → optional
        self.assertTrue(by_path["Identifiers/ISBN13"]["required"])
        self.assertTrue(by_path["Price"]["required"])
        self.assertFalse(by_path["Identifiers/ISBN10"]["required"])
        self.assertFalse(by_path["Title"]["required"])
        # attributes: on the record itself and on a leaf
        self.assertTrue(by_path["@id"]["required"])
        self.assertIn("Title@lang", by_path)
        # repeats and types come from the values
        self.assertIn('name="Author" maxOccurs="unbounded"', xsd)
        self.assertIn('name="Price" type="xs:decimal"', xsd)
        self.assertIn('name="Published" type="xs:date"', xsd)
        self.assertIn('name="ISBN13" type="xs:integer"', xsd)
        self.assertIn('name="Title"', xsd)
        self.assertIn('<xs:extension base="xs:string">', xsd)
        self.assertTrue(any("urn:partner:catalog" in n for n in notes))
        self.assertTrue(any("2 'Product' records" in n for n in notes))

    def test_mixed_values_fall_back_to_string_and_mixed_content_is_noted(self):
        xsd, info, error = partner_schema.infer_xsd_from_xml(
            "<R><Item><Code>12</Code></Item><Item><Code>AB</Code></Item>"
            "<Note>text <b>bold</b></Note></R>")
        self.assertIsNone(error)
        self.assertIn('name="Code" type="xs:string"', xsd)
        self.assertTrue(any("'Note'" in n and "both text and child" in n
                            for n in info["notes"]))
        # no single repeating child: the root itself is the record
        self.assertEqual((info["wrapper"], info["record"]), (None, "R"))

    def test_refusals_are_readable(self):
        for text, fragment in [("<Products/>", "no elements"),
                               ("<!DOCTYPE x []><P><a/></P>", "DOCTYPE"),
                               ("<broken", "well-formed"),
                               (SIMPLE, "already an XML Schema")]:
            _, _, error = partner_schema.infer_xsd_from_xml(text)
            self.assertIn(fragment, error)

    def test_looks_like_sample(self):
        self.assertTrue(partner_schema.looks_like_sample(SAMPLE))
        self.assertFalse(partner_schema.looks_like_sample(SIMPLE))
        self.assertFalse(partner_schema.looks_like_sample("<broken"))


class TestLocalStaging(unittest.TestCase):
    def setUp(self):
        os.environ.pop("BEACON_DATA_BUCKET", None)
        partner_schema.reset_local_for_testing()

    def test_infer_returns_the_schema_without_storing_it(self):
        """UIFPE-660: the drawer's call. The upload itself stays XSD-only —
        "Use it for this export" sends the built file through POST /schemas."""
        response = client.post("/schemas/infer", json={"filename": "catalog.xml",
                                                       "content": SAMPLE})
        self.assertEqual(response.status_code, 200)
        record = response.json()
        self.assertEqual(record["filename"], "catalog.inferred.xsd")
        self.assertEqual(record["sample_filename"], "catalog.xml")
        self.assertEqual((record["root_tag"], record["row_tag"]), ("Products", "Product"))
        self.assertEqual(record["records"], 2)
        self.assertTrue(record["rows"] and record["notes"])
        self.assertNotIn("id", record)  # nothing staged
        # the built file is an ordinary XSD to the upload
        staged, error = partner_schema.stage_upload(record["filename"], record["xsd"])
        self.assertIsNone(error)
        self.assertEqual(staged["row_tag"], "Product")
        # a sample sent straight to the upload is still refused, as before
        _, error = partner_schema.stage_upload("catalog.xml", SAMPLE)
        self.assertIn("xs:schema", error)
        bad = client.post("/schemas/infer", json={"filename": "x.xml", "content": "<Products/>"})
        self.assertEqual(bad.status_code, 422)
        self.assertIn("no elements", bad.json()["detail"])

    def test_upload_roundtrip_and_describe(self):
        record, error = partner_schema.stage_upload("partner.xsd", SIMPLE)
        self.assertIsNone(error)
        self.assertEqual(record["filename"], "partner.xsd")
        self.assertEqual(record["sha256"], partner_schema.sha256_of(SIMPLE))
        described, error = partner_schema.describe_upload(record["id"])
        self.assertIsNone(error)
        self.assertEqual(described["row_tag"], "Product")

    def test_unknown_reference(self):
        _, error = partner_schema.describe_upload("0" * 32)
        self.assertIn("unknown", error)
        _, error = partner_schema.describe_upload("not-a-ref")
        self.assertIn("unknown", error)

    def test_endpoints(self):
        response = client.post("/schemas", json={"filename": "p.xsd",
                                                 "content": SIMPLE})
        self.assertEqual(response.status_code, 201)
        schema_id = response.json()["id"]
        self.assertEqual(client.get(f"/schemas/{schema_id}").status_code, 200)
        self.assertEqual(client.get(f"/schemas/{'0' * 32}").status_code, 404)
        bad = client.post("/schemas", json={"filename": "s.xml",
                                            "content": "<Products/>"})
        self.assertEqual(bad.status_code, 422)

    def test_storage_failure_is_a_readable_503_not_a_bare_500(self):
        # the dev incident: a denied PutObject escaped as a bare 500, which
        # carries no CORS headers — the browser then blamed CORS
        class RefusingS3:
            def put_object(self, **kw):
                raise RuntimeError("AccessDenied")

        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        publisher.set_clients_for_testing(RefusingS3(), None, None)
        try:
            response = client.post("/schemas", json={"filename": "p.xsd",
                                                     "content": SIMPLE})
            self.assertEqual(response.status_code, 503)
            self.assertIn("could not be stored", response.json()["detail"])
        finally:
            os.environ.pop("BEACON_DATA_BUCKET", None)
            publisher.set_clients_for_testing(None, None)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[f"{Bucket}/{Key}"] = Body

    def get_object(self, Bucket, Key):
        path = f"{Bucket}/{Key}"
        if path not in self.objects:
            raise KeyError(path)
        body = self.objects[path]
        if isinstance(body, str):
            body = body.encode("utf-8")
        return {"Body": io.BytesIO(body)}

    def head_object(self, Bucket, Key):
        if f"{Bucket}/{Key}" not in self.objects:
            raise KeyError(Key)
        return {}


class TestFreezeAndPublishGate(unittest.TestCase):
    def setUp(self):
        lifecycle.reset()
        partner_schema.reset_local_for_testing()
        self.s3 = FakeS3()
        publisher.set_clients_for_testing(self.s3, None, None)
        os.environ["BEACON_DATA_BUCKET"] = "demo-bucket"
        os.environ["BEACON_CONFIG_PREFIX"] = "beacon-demo/configs"

    def tearDown(self):
        for key in ("BEACON_DATA_BUCKET", "BEACON_CONFIG_PREFIX"):
            os.environ.pop(key, None)
        publisher.set_clients_for_testing(None, None)

    def _staged(self, text):
        return client.post("/schemas",
                           json={"filename": "p.xsd", "content": text}).json()

    def test_freeze_writes_once_and_is_idempotent(self):
        staged = self._staged(SIMPLE)
        frozen, error = partner_schema.freeze("j1", 1, staged["id"],
                                              staged["sha256"])
        self.assertIsNone(error)
        key = "demo-bucket/beacon-demo/configs/j1/v1/schema.xsd"
        self.assertIn(key, self.s3.objects)
        again, error = partner_schema.freeze("j1", 1, staged["id"],
                                             staged["sha256"])
        self.assertIsNone(error)  # same bytes: already done
        self.s3.objects[key] = "tampered"
        _, error = partner_schema.freeze("j1", 1, staged["id"], staged["sha256"])
        self.assertIn("refusing to overwrite", error)

    def test_freeze_refuses_a_changed_staged_file(self):
        staged = self._staged(SIMPLE)
        _, error = partner_schema.freeze("j1", 1, staged["id"], "wrong-hash")
        self.assertIn("changed since", error)

    def test_publish_freezes_schema_and_stamps_export_config(self):
        staged = self._staged(WRAPPED)  # requires productId only — bound
        draft = make_draft(settings={
            "formats": ["xml"],
            "partner_schema": {"id": staged["id"], "sha256": staged["sha256"],
                               "filename": "p.xsd"}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertTrue(version["s3"]["published"], version["s3"])
        base = f"demo-bucket/beacon-demo/configs/{version['job_id']}/v1"
        self.assertIn(f"{base}/schema.xsd", self.s3.objects)
        export_cfg = json.loads(self.s3.objects[f"{base}/export.json"])
        self.assertEqual(export_cfg["partner_schema"]["sha256"],
                         staged["sha256"])
        self.assertTrue(export_cfg["partner_schema"]["s3_uri"]
                        .endswith("/v1/schema.xsd"))

    def test_publish_refuses_unbound_required_elements(self):
        staged = self._staged(DEMANDING)  # requires productId AND Isbn
        draft = make_draft(settings={
            "formats": ["xml"],
            "partner_schema": {"id": staged["id"], "sha256": staged["sha256"],
                               "filename": "p.xsd"}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertFalse(version["s3"]["published"])
        self.assertIn("Isbn", version["s3"]["reason"])
        base = f"demo-bucket/beacon-demo/configs/{version['job_id']}/v1"
        self.assertNotIn(f"{base}/schema.xsd", self.s3.objects)

    def test_publish_warns_about_undeclared_elements_but_still_publishes(self):
        """UIFPE-587: a warning, not a refusal — and recorded on the version."""
        staged = self._staged(WRAPPED)  # declares productId only
        config = dict(MAPPING)
        config["mappings"] = [*MAPPING["mappings"],
                              {"type": "one_to_one",
                               "source": "product.product_id",
                               "target": "organization_code"}]
        draft = make_draft(
            mapping_config=config,
            settings={"formats": ["xml"],
                      "partner_schema": {"id": staged["id"],
                                         "sha256": staged["sha256"],
                                         "filename": "p.xsd"}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertTrue(version["s3"]["published"], version["s3"])
        warnings = version["s3"]["schema_warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("organization_code", warnings[0])
        self.assertIn("does not declare", warnings[0])

    def test_publish_is_silent_when_every_target_is_declared(self):
        staged = self._staged(WRAPPED)
        draft = make_draft(settings={
            "formats": ["xml"],
            "partner_schema": {"id": staged["id"], "sha256": staged["sha256"],
                               "filename": "p.xsd"}})
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertTrue(version["s3"]["published"])
        self.assertNotIn("schema_warnings", version["s3"])

    def test_publish_without_schema_is_untouched(self):
        draft = make_draft()
        version = client.post(f"/drafts/{draft['id']}/submit").json()
        self.assertTrue(version["s3"]["published"])
        export_cfg = json.loads(self.s3.objects[
            f"demo-bucket/beacon-demo/configs/{version['job_id']}/v1/export.json"])
        self.assertNotIn("partner_schema", export_cfg)


if __name__ == "__main__":
    unittest.main()
