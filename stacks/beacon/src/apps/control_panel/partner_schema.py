# partner_schema.py (UIFPE-558)
# The partner's XSD as a first-class part of an export. Upload validates and
# stages the file (data bucket, schemas/uploads/<id>.xsd — a DRAFT is one
# DynamoDB item with a 384 KB cap, so the draft carries only a reference and
# a content hash); publish freezes the exact bytes into the immutable
# version folder beside the config pair (D22). The wizard reads the parsed
# tree to guide mapping, and required elements are enforced at publish.
#
# XSD support is deliberately the COMMON CORE: elements, attributes,
# nesting, required/optional (minOccurs), repeats (maxOccurs), named
# complex types and element refs within the one file. Anything beyond that
# (import/include, substitution groups, choice semantics) is REPORTED in
# `unsupported`, never silently ignored — the author sees what the guide
# does not cover. Security posture: the file is data, never instructions —
# a DOCTYPE (the XXE vector) refuses the upload outright, and nothing is
# ever fetched from inside the file.

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from xml.etree import ElementTree

XS = "{http://www.w3.org/2001/XMLSchema}"
MAX_XSD_BYTES = 2 * 1024 * 1024
UPLOAD_PREFIX = "schemas/uploads"

# local mode (no data bucket): staged uploads live in memory, like the
# destinations registry's local fallback — keeps the suite and local dev AWS-free
_local_uploads: dict[str, dict] = {}

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _bucket() -> str | None:
    return os.environ.get("BEACON_DATA_BUCKET") or None


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- parsing -----------------------------------------------------------------

def parse_xsd(text: str) -> tuple[dict | None, str | None]:
    """(schema description, None) or (None, readable refusal).

    The description: {"root": <node>, "unsupported": [notes]} where a node is
    {"name", "required", "repeats", "attributes": [{"name", "required"}],
    "children": [nodes], "leaf": bool}."""
    if not isinstance(text, str) or not text.strip():
        return None, "the file is empty"
    if len(text.encode("utf-8")) > MAX_XSD_BYTES:
        return None, (f"the schema is larger than "
                      f"{MAX_XSD_BYTES // (1024 * 1024)} MB — check the file")
    if "<!DOCTYPE" in text:
        return None, ("the file carries a DOCTYPE declaration, which schemas "
                      "do not need — remove it and upload again")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        return None, f"not well-formed XML: {e}"
    if root.tag != f"{XS}schema":
        return None, ("not an XML Schema — the root element must be "
                      "xs:schema (this looks like a sample document; Phase 1 "
                      "path targets cover that case)")

    unsupported: list[str] = []
    for child in root:
        local = child.tag.split("}")[-1]
        if child.tag in (f"{XS}import", f"{XS}include", f"{XS}redefine"):
            unsupported.append(f"xs:{local} is not followed — the guide "
                               "covers this file only")
    named_types = {t.get("name"): t for t in root.findall(f"{XS}complexType")
                   if t.get("name")}
    top_elements = {e.get("name"): e for e in root.findall(f"{XS}element")
                    if e.get("name")}
    if not top_elements:
        return None, "the schema declares no top-level element"

    def build(el, seen: tuple[str, ...]) -> dict:
        name = el.get("name") or ""
        if el.get("ref"):
            ref = el.get("ref").split(":")[-1]
            if ref in top_elements and ref not in seen:
                built = build(top_elements[ref], seen)
                built["required"] = el.get("minOccurs", "1") != "0"
                built["repeats"] = el.get("maxOccurs", "1") not in ("0", "1")
                return built
            unsupported.append(f"element ref {el.get('ref')!r} could not be "
                               "followed")
            name = ref
        node = {"name": name,
                "required": el.get("minOccurs", "1") != "0",
                "repeats": el.get("maxOccurs", "1") not in ("0", "1"),
                "attributes": [], "children": []}
        if name in seen:  # a recursive schema: stop the walk, say so
            unsupported.append(f"recursive element {name!r} is shown once, "
                               "not expanded")
            node["leaf"] = True
            return node
        if el.get("substitutionGroup"):
            unsupported.append(f"substitutionGroup on {name!r} is ignored")
        ctype = el.find(f"{XS}complexType")
        if ctype is None and el.get("type"):
            tname = el.get("type").split(":")[-1]
            ctype = named_types.get(tname)  # xs:string etc. = a plain leaf
        if ctype is not None:
            for attr in ctype.findall(f"{XS}attribute"):
                node["attributes"].append(
                    {"name": attr.get("name") or attr.get("ref", "?"),
                     "required": attr.get("use") == "required"})
            simple = ctype.find(f"{XS}simpleContent")
            if simple is not None:
                ext = simple.find(f"{XS}extension")
                if ext is not None:
                    for attr in ext.findall(f"{XS}attribute"):
                        node["attributes"].append(
                            {"name": attr.get("name") or attr.get("ref", "?"),
                             "required": attr.get("use") == "required"})
            for group_tag in ("sequence", "all", "choice"):
                group = ctype.find(f"{XS}{group_tag}")
                if group is None:
                    continue
                if group_tag == "choice":
                    unsupported.append(
                        f"xs:choice under {name!r}: its options are listed "
                        "as ordinary optional children")
                for sub in group.findall(f"{XS}element"):
                    child = build(sub, seen + (name,))
                    if group_tag == "choice":
                        child["required"] = False
                    node["children"].append(child)
        node["leaf"] = not node["children"]
        return node

    first = next(iter(top_elements.values()))
    tree = build(first, ())
    if len(top_elements) > 1:
        unsupported.append(
            "the schema declares several top-level elements — the guide "
            f"follows the first ({tree['name']!r})")
    return {"root": tree, "unsupported": unsupported}, None


def record_shape(schema: dict) -> dict:
    """Which element is one RECORD (one data row), and the tag suggestions.

    If the document root wraps exactly one repeating child, that child is the
    record (root_tag = the wrapper, row_tag = the record). Otherwise the root
    itself is the record and the wrapper tag stays the author's choice."""
    root = schema["root"]
    repeating = [c for c in root["children"] if c.get("repeats")]
    if len(repeating) == 1 and len(root["children"]) == 1:
        return {"root_tag": root["name"], "row_tag": repeating[0]["name"],
                "record": repeating[0]}
    return {"root_tag": None, "row_tag": root["name"], "record": root}


def _walk_paths(node: dict, prefix: str, out: list[dict]) -> None:
    # prefix is always the node's own path here (schema_paths seeds it with
    # the record's children; the record's own attributes are handled there)
    for attr in node["attributes"]:
        out.append({"path": f"{prefix}@{attr['name']}",
                    "required": attr["required"] and node["required"],
                    "kind": "attribute"})
    if node["leaf"]:
        if prefix:
            out.append({"path": prefix, "required": node["required"],
                        "kind": "element"})
        return
    for child in node["children"]:
        child_prefix = f"{prefix}/{child['name']}" if prefix else child["name"]
        # a child's requiredness only binds while every ancestor is required
        effective = dict(child)
        effective["required"] = child["required"] and node["required"]
        _walk_paths(effective, child_prefix, out)


def schema_paths(schema: dict) -> dict:
    """Every bindable path relative to the record element, with required
    flags — the wizard's tree source and the publish gate's checklist.

    `declared` is the wider set: every path the schema mentions at ANY level,
    which is what decides whether a mapping target is known (UIFPE-587).
    `paths` holds only the bindable leaves and attributes."""
    shape = record_shape(schema)
    record = shape["record"]
    out: list[dict] = []
    for attr in record["attributes"]:
        out.append({"path": f"@{attr['name']}", "required": attr["required"],
                    "kind": "attribute"})
    for child in record["children"]:
        effective = dict(child)
        _walk_paths(effective, child["name"], out)
    return {"root_tag": shape["root_tag"], "row_tag": shape["row_tag"],
            "paths": out, "declared": sorted(declared_paths(schema))}


def declared_paths(schema: dict) -> set[str]:
    """Every path the schema declares relative to the record: elements at
    EVERY level, not just the leaves, plus attributes.

    Inner elements count as declared on purpose. Binding a structured column
    to an inner element is legitimate — it writes that column's fields as
    child elements, which is how a contributor block reached the first real
    dev delivery — so it must not be reported as something the schema never
    mentioned."""
    out: set[str] = set()

    def walk(node: dict, prefix: str) -> None:
        for attr in node["attributes"]:
            out.add(f"{prefix}@{attr['name']}" if prefix else f"@{attr['name']}")
        for child in node["children"]:
            path = f"{prefix}/{child['name']}" if prefix else child["name"]
            out.add(path)
            walk(child, path)

    walk(record_shape(schema)["record"], "")
    return out


def unknown_targets(schema: dict, targets) -> list[str]:
    """Mapping targets the schema does not declare, in the order given.

    A WARNING, never a refusal (UIFPE-587): a partner's schema may lag behind
    what the author needs, and refusing would block an export that is
    deliberately ahead of it. But a strict `xs:sequence` rejects a document
    with an undeclared element, so silence is the wrong answer too."""
    declared = declared_paths(schema)
    seen: set[str] = set()
    out: list[str] = []
    for target in targets:
        name = str(target)
        if name and name not in declared and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def missing_required(schema: dict, targets) -> list[str]:
    """Required paths not bound by any mapping target. Record-level
    attributes (paths starting with '@') cannot be bound in Phase 1 and are
    reported so the author knows."""
    bound = {str(t) for t in targets}
    return [p["path"] for p in schema_paths(schema)["paths"]
            if p["required"] and p["path"] not in bound]


# ---- a sample document instead of a schema (UIFPE-660) ------------------------
# Many partners send an example file, not an XSD. The sample is read once,
# every occurrence of an element is merged, and the LOOSEST schema that fits
# is written as a real XSD: a child is required only if every parent instance
# has it, repeats only if some parent has more than one, and a leaf gets a
# typed xs: type only when every non-empty value agrees. What one sample
# cannot prove is said in `notes`. Downstream (staging, freeze, guide, gate)
# sees an ordinary XSD.

_XSI = "{http://www.w3.org/2001/XMLSchema-instance}"
_INT_RE = re.compile(r"^[+-]?\d+$")
_DEC_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$")
_BOOL_RE = re.compile(r"^(true|false)$")


def _local(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else str(tag)


def looks_like_sample(text: str) -> bool:
    """Well-formed XML whose root is not xs:schema — a document, not a schema."""
    if not isinstance(text, str) or "<!DOCTYPE" in text:
        return False
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return False
    return root.tag != f"{XS}schema"


def _value_type(values: list[str]) -> str:
    if not values:
        return "xs:string"
    if all(_INT_RE.match(v) for v in values):
        return "xs:integer"
    if all(_INT_RE.match(v) or _DEC_RE.match(v) for v in values):
        return "xs:decimal"
    for regex, xs_type in ((_DATE_RE, "xs:date"), (_DATETIME_RE, "xs:dateTime"),
                           (_BOOL_RE, "xs:boolean")):
        if all(regex.match(v) for v in values):
            return xs_type
    return "xs:string"


def _profile(elements: list, node: dict, namespaces: set[str], notes: list[str]) -> None:
    """Merge every instance of one element path into `node`."""
    node["instances"] = len(elements)
    for el in elements:
        if "}" in el.tag:
            namespaces.add(el.tag[1:el.tag.index("}")])
        for key in el.attrib:
            if key.startswith(_XSI):
                continue  # xsi:schemaLocation and friends describe the file, not the data
            name = _local(key)
            node["attrs"][name] = node["attrs"].get(name, 0) + 1
        counts: dict[str, int] = {}
        for child in el:
            if not isinstance(child.tag, str):
                continue  # comments and processing instructions
            name = _local(child.tag)
            node["children"].setdefault(name, {"name": name, "attrs": {}, "children": {},
                                               "values": [], "present_in": 0,
                                               "max_per_parent": 1})
            counts[name] = counts.get(name, 0) + 1
        for name, count in counts.items():
            child_node = node["children"][name]
            child_node["present_in"] += 1
            child_node["max_per_parent"] = max(child_node["max_per_parent"], count)
        text = (el.text or "").strip()
        if text:
            if len(el):
                if not node.get("mixed"):
                    node["mixed"] = True
                    notes.append(f"{node['name']!r} carries both text and child "
                                 "elements in the sample — only the elements are kept")
            else:
                node["values"].append(text)
    for name, child_node in node["children"].items():
        kids = [c for el in elements for c in el
                if isinstance(c.tag, str) and _local(c.tag) == name]
        _profile(kids, child_node, namespaces, notes)


def _emit(node: dict, parent, *, top: bool, required: bool = True,
          repeats: bool = False) -> None:
    el = ElementTree.SubElement(parent, f"{XS}element", name=node["name"])
    if not top:
        if not required:
            el.set("minOccurs", "0")
        if repeats:
            el.set("maxOccurs", "unbounded")
    n = node["instances"]
    attrs = node["attrs"]
    if not node["children"] and not attrs:
        el.set("type", _value_type(node["values"]))
        return
    ctype = ElementTree.SubElement(el, f"{XS}complexType")
    if node["children"]:
        seq = ElementTree.SubElement(ctype, f"{XS}sequence")
        for child in node["children"].values():
            _emit(child, seq, top=False, required=child["present_in"] == n,
                  repeats=child["max_per_parent"] > 1)
        holder = ctype
    else:  # a leaf with attributes: text of one type, plus the attributes
        simple = ElementTree.SubElement(ctype, f"{XS}simpleContent")
        holder = ElementTree.SubElement(simple, f"{XS}extension",
                                        base=_value_type(node["values"]))
    for name, count in attrs.items():
        ElementTree.SubElement(holder, f"{XS}attribute", name=name, type="xs:string",
                               use="required" if count == n else "optional")


def _rows(node: dict, prefix: str, parent_n: int, parent_required: bool,
          out: list[dict]) -> None:
    """The bindable paths under `node` as the drawer lists them: required /
    optional, repeats, and the value type a leaf was given."""
    n = node["instances"]
    for name, count in node["attrs"].items():
        out.append({"path": f"{prefix}@{name}", "kind": "attribute",
                    "required": parent_required and count == n, "repeats": False,
                    "type": "string"})
    for child in node["children"].values():
        path = f"{prefix}/{child['name']}" if prefix else child["name"]
        required = parent_required and child["present_in"] == n
        repeats = child["max_per_parent"] > 1
        if child["children"]:
            _rows(child, path, child["instances"], required, out)
            continue
        for name, count in child["attrs"].items():
            out.append({"path": f"{path}@{name}", "kind": "attribute",
                        "required": required and count == child["instances"],
                        "repeats": False, "type": "string"})
        out.append({"path": path, "kind": "element", "required": required,
                    "repeats": repeats,
                    "type": _value_type(child["values"]).split(":")[-1]})


def infer_xsd_from_xml(text: str) -> tuple[str | None, dict | None, str | None]:
    """(xsd text, what was read, None) or (None, None, readable refusal).

    What was read: {"notes": [..], "wrapper": name|None, "record": name,
    "records": n, "rows": [{path, kind, required, repeats, type}],
    "elements": n, "attributes": n} — the drawer's facts and tree."""
    if not isinstance(text, str) or not text.strip():
        return None, None, "the file is empty"
    if len(text.encode("utf-8")) > MAX_XSD_BYTES:
        return None, None, (f"the sample is larger than "
                            f"{MAX_XSD_BYTES // (1024 * 1024)} MB — a few records are enough")
    if "<!DOCTYPE" in text:
        return None, None, ("the file carries a DOCTYPE declaration, which a sample "
                            "does not need — remove it and upload again")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        return None, None, f"not well-formed XML: {e}"
    if root.tag == f"{XS}schema":
        return None, None, "this is already an XML Schema"

    notes: list[str] = []
    namespaces: set[str] = set()
    tree = {"name": _local(root.tag), "attrs": {}, "children": {}, "values": [],
            "present_in": 1, "max_per_parent": 1}
    _profile([root], tree, namespaces, notes)
    if not tree["children"] and not tree["attrs"]:
        return None, None, ("the sample has no elements to map — the record needs "
                            "at least one child element or attribute")
    if namespaces:
        notes.append("namespace(s) " + ", ".join(sorted(namespaces)) +
                     " dropped — the schema built from the sample has no target namespace")
    children = tree["children"]
    only = next(iter(children.values())) if len(children) == 1 else None
    if only is not None and only["max_per_parent"] > 1:
        wrapper, record = tree["name"], only
        notes.append(f"required / optional and repeats were read from "
                     f"{record['instances']} {record['name']!r} records in the sample")
    else:
        wrapper, record = None, tree
        notes.append("the sample holds one record, so every element it has counts as "
                     "required — upload the partner's XSD later to say otherwise")
    rows: list[dict] = []
    _rows(record, "", record["instances"], True, rows)

    ElementTree.register_namespace("xs", XS[1:-1])
    schema = ElementTree.Element(f"{XS}schema", elementFormDefault="qualified")
    _emit(tree, schema, top=True)
    if hasattr(ElementTree, "indent"):
        ElementTree.indent(schema)
    xsd = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           + ElementTree.tostring(schema, encoding="unicode"))
    info = {"notes": notes, "wrapper": wrapper, "record": record["name"],
            "records": record["instances"], "rows": rows,
            "elements": sum(1 for r in rows if r["kind"] == "element"),
            "attributes": sum(1 for r in rows if r["kind"] == "attribute")}
    return xsd, info, None


def infer_schema(filename: str, content: str) -> tuple[dict | None, str | None]:
    """The drawer's one call: the XSD built from a sample plus what was read.
    Nothing is stored — the author downloads the file, or hands it to the
    ordinary upload ("Use it for this export"), which stages it like any XSD."""
    xsd, info, error = infer_xsd_from_xml(content)
    if error:
        return None, error
    schema, error = parse_xsd(xsd)
    if error:  # our own output failed our own parser: a bug, said plainly
        return None, f"the schema built from the sample did not parse: {error}"
    base = re.sub(r"\.xml$", "", (filename or "sample").strip() or "sample", flags=re.I)
    return {"xsd": xsd, "filename": f"{base[:180]}.inferred.xsd",
            "sample_filename": (filename or "")[:200], "bytes": len(xsd.encode("utf-8")),
            **info, **schema_paths(schema), "unsupported": schema["unsupported"]}, None


# ---- staging and freezing ----------------------------------------------------

def stage_upload(filename: str, content: str) -> tuple[dict | None, str | None]:
    # an XSD only — a sample goes through infer_schema first (UIFPE-660), and
    # what comes back here is the generated XSD like any other
    schema, error = parse_xsd(content)
    if error:
        return None, error
    upload_id = uuid.uuid4().hex
    digest = sha256_of(content)
    bucket = _bucket()
    if bucket:
        s3, _ = _publisher_clients()  # the publisher's shared s3 client
        s3.put_object(Bucket=bucket, Key=f"{UPLOAD_PREFIX}/{upload_id}.xsd",
                      Body=content.encode("utf-8"),
                      ContentType="application/xml")
    else:
        _local_uploads[upload_id] = {"content": content}
    return {"id": upload_id, "sha256": digest,
            "filename": (filename or "schema.xsd")[:200],
            **schema_paths(schema),
            "unsupported": schema["unsupported"]}, None


def _publisher_clients():
    from control_panel import publisher
    return publisher._clients()


def read_upload(upload_id: str) -> tuple[str | None, str | None]:
    """The staged file's content, or a readable reason."""
    if not _ID_RE.match(upload_id or ""):
        return None, "unknown schema reference"
    bucket = _bucket()
    if not bucket:
        row = _local_uploads.get(upload_id)
        return (row["content"], None) if row else (None, "unknown schema reference")
    try:
        s3, _ = _publisher_clients()
        body = s3.get_object(Bucket=bucket,
                             Key=f"{UPLOAD_PREFIX}/{upload_id}.xsd")["Body"].read()
        return body.decode("utf-8"), None
    except Exception:
        return None, ("the staged schema could not be read — upload it "
                      "again from the wizard")


def describe_upload(upload_id: str) -> tuple[dict | None, str | None]:
    content, error = read_upload(upload_id)
    if error:
        return None, error
    schema, error = parse_xsd(content)
    if error:  # stored by us after parsing, so this is corruption, not input
        return None, f"the staged schema no longer parses: {error}"
    return {"id": upload_id, "sha256": sha256_of(content),
            **schema_paths(schema),
            "unsupported": schema["unsupported"]}, None


def freeze(job_id: str, version: int, upload_id: str,
           expected_sha256: str | None) -> tuple[dict | None, str | None]:
    """Copy the staged schema into the immutable version folder as
    configs/<job>/v<N>/schema.xsd. Same posture as the config pair: an
    existing key is never overwritten, and the bytes must still match the
    hash the draft recorded."""
    content, error = read_upload(upload_id)
    if error:
        return None, error
    digest = sha256_of(content)
    if expected_sha256 and digest != expected_sha256:
        return None, ("the staged schema changed since it was attached to "
                      "this draft — re-upload it and validate again")
    bucket = _bucket()
    if not bucket:  # catalog-only publish: nothing to freeze into
        return {"frozen": False, "sha256": digest}, None
    prefix = os.environ.get("BEACON_CONFIG_PREFIX", "configs").strip("/")
    key = f"{prefix}/{job_id}/v{version}/schema.xsd"
    s3, _ = _publisher_clients()
    try:
        existing = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        # a publish retry of the same version meets its own earlier freeze:
        # identical bytes = already done (idempotent); different bytes = the
        # immutability breach we refuse loudly, like the config pair
        if sha256_of(existing.decode("utf-8")) == digest:
            return {"frozen": True, "s3_key": key, "sha256": digest}, None
        return None, (f"refusing to overwrite existing immutable schema "
                      f"s3://{bucket}/{key} with different content")
    except Exception:
        pass  # 404 = free
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"),
                  ContentType="application/xml")
    return {"frozen": True, "s3_key": key, "sha256": digest}, None


def reset_local_for_testing() -> None:
    _local_uploads.clear()
