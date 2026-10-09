"""contract_bundle.py - the authoritative REST contract of a domain and its GENERATED bundle.

THE CONTRACT (every domain, no exceptions, no registry):
    domains/<domain>/src/api/openapi.yaml
      + every LOCAL external $ref dependency reachable from it, recursively
An unreferenced file under src/api is NOT part of the contract. A developer keeps authoring
multi-file contracts exactly as today; nothing here rewrites a source file.

THE BUNDLE (a TRANSIENT BUILD ARTIFACT, never committed): one self-contained OpenAPI 3.1
document in which every external target - a whole schema file or a JSON-Pointer fragment of
one - is lifted into components/schemas under a deterministic name and every reference to it
is rewritten to an internal `#/components/schemas/<name>` reference. Release comparison,
OAS 3.0 projection, Exchange publication, MCP generation and the contract gate all consume
this ONE document, so a dependency-only change is a contract change and a missing or unsafe
dependency fails before anything is published. Bridge reconciliation never sees it.

$ref FORMS (the file portion feeds the dependency graph, the fragment feeds resolution):
    #/components/schemas/Foo            INTERNAL  same document, left alone in the root
    ./foo.json  ./foo.json#/definitions/Foo  ../../schemas/channels/<d>/x.json  ../common/bar.yaml#/Bar
                                        LOCAL     resolved relative to the REFERRING FILE, canonical
                                                  real path must stay under the CONTRACT ROOT = the
                                                  domain's src/ directory (domains/<domain>/src/)
    /etc/x.yaml  C:\\x.json  file://  http://  https://  (and any scheme or authority)
                                        UNSAFE    refused

SUPPORTED SUBSET (documented, fail-closed outside it):
  * relative references resolve against the physical referring file. A dependency that declares
    `$id` AND contains relative external references is REFUSED: JSON Schema base-URI semantics
    could resolve them elsewhere and this module will not guess. `$id` without such references
    (the common generated-schema case) is fine.
  * the destination components/<section> of an external target is decided by the SEMANTIC
    CONTEXT of the reference (schema -> schemas, parameters[] -> parameters, responses.<code> ->
    responses, requestBody -> requestBodies, headers.<n> -> headers, examples.<n> -> examples,
    links.<n> -> links, callbacks.<n> -> callbacks, securitySchemes.<n> -> securitySchemes),
    NEVER by inspecting the target's content. A reference in a context whose object type is
    unknown is REFUSED; an external PathItem reference is REFUSED (no 3.0.3 representation);
    one target referenced under two different types is REFUSED. Lifted content is walked in
    its own context, so a lifted Response's `content.*.schema` external ref becomes a Schema.
    `$defs`/`definitions` containers are never emitted - their referenced members are lifted.
  * lifted content IN SCHEMA CONTEXT gets the GENERIC schema-object compatibility policy: JSON Schema / OAS
    keywords are kept verbatim (the OAS 3.0 projection still decides what it can translate);
    the document identifiers $schema/$id/$comment and ANY unknown non-keyword key become
    `x-<key>` vendor extensions (lossless; OAS permits them on Schema Objects); keywords that
    change reference semantics ($anchor, $dynamicRef, ...) are REFUSED. Property NAMES under
    `properties` & co. are data, never keywords, and are never touched.
  * file cycles (a.yaml -> b.yaml -> a.yaml) are fine: each target is lifted once. Schema
    recursion is preserved as internal references (the MCP tool-schema inliner has its own,
    pre-existing limit for request bodies).
  * a generated component name that collides with an authored component, or two different
    targets deriving the same name, FAILS CLOSED - never a silent rename.

CONTRACT ROOT: contract_root(path) = the nearest ancestor directory named `src` of the root
OpenAPI (domains/<domain>/src/), else the root's own directory. The repository layout is
domains/<domain>/src/api/<api>/openapi.yaml referencing domains/<domain>/src/schemas/...,
so a domain may reference any file it owns under src/ and nothing outside it.

Pure functions + stdlib + PyYAML. No network. Reads files only under the contract root.
"""
import copy
import json
import os
import re
from urllib.parse import urlsplit

import yaml

INTERNAL, LOCAL, UNSAFE = "internal", "local", "unsafe"
COMPONENTS_SECTION = "schemas"

_DRIVE = re.compile(r"^[A-Za-z]:")
_COMPONENT_KEY = re.compile(r"[^A-Za-z0-9._-]")

# schema-object vocabulary (JSON Schema 2020-12 + OAS 3.0/3.1 Schema Object fixed fields)
_MAP_OF_SCHEMAS = {"properties", "patternProperties", "dependentSchemas"}
_CONTAINERS = {"$defs", "definitions"}  # pointer targets only; members are lifted on demand, never emitted
_LIST_OF_SCHEMAS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SINGLE_SCHEMA = {"items", "not", "additionalProperties", "additionalItems", "contains", "propertyNames", "if", "then", "else",
                  "unevaluatedItems", "unevaluatedProperties", "contentSchema"}
_VALUE_KEYWORDS = {"$ref", "type", "title", "description", "default", "examples", "example", "enum", "const", "format", "multipleOf",
                   "maximum", "exclusiveMaximum", "minimum", "exclusiveMinimum", "maxLength", "minLength", "pattern", "maxItems",
                   "minItems", "uniqueItems", "maxContains", "minContains", "maxProperties", "minProperties", "required",
                   "dependentRequired", "contentEncoding", "contentMediaType", "readOnly", "writeOnly", "deprecated", "nullable",
                   "discriminator", "xml", "externalDocs"}
_IDENTIFIERS = {"$schema", "$id", "$comment"}  # document identity / annotation: relocated to x- (no validation meaning once embedded)
_REF_SEMANTICS = {"$anchor", "$dynamicAnchor", "$dynamicRef", "$recursiveRef", "$recursiveAnchor", "$vocabulary"}


class ContractError(ValueError):
    """The contract cannot be resolved or bundled safely; the message says exactly why."""


class MissingDependency(ContractError, FileNotFoundError):
    """A referenced local file does not exist."""


# ── classification and safe resolution ─────────────────────────────────────────

def classify_ref(ref):
    """(kind, file_part, fragment). kind is INTERNAL (empty file part), LOCAL (a relative file
    path, forward slashes) or UNSAFE (scheme, authority, absolute POSIX/Windows path, drive)."""
    if not isinstance(ref, str):
        raise ContractError("$ref must be a string, got %r" % (ref,))
    file_part, _, fragment = ref.partition("#")
    if not file_part:
        return INTERNAL, "", fragment
    if "\\" in file_part or file_part.startswith("/") or _DRIVE.match(file_part):
        return UNSAFE, file_part, fragment
    parts = urlsplit(file_part)
    if parts.scheme or parts.netloc:
        return UNSAFE, file_part, fragment
    return LOCAL, file_part, fragment


def contract_root(path):
    """The containment boundary for a root contract: its nearest ancestor directory named `src`
    (the domain's src/), else its own directory. Canonical (symlinks resolved)."""
    here = os.path.dirname(os.path.realpath(os.path.abspath(path)))
    probe = here
    while True:
        if os.path.basename(probe) == "src":
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return here
        probe = parent


def canonical(path):
    """Absolute real path (symlinks resolved), case-normalised for comparison on this OS."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def inside(path, root):
    p, r = canonical(path), canonical(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def resolve_local(file_part, base_dir, api_root, ref=None):
    """Canonical path of a LOCAL reference resolved against `base_dir` (the referring file's
    directory). Refuses anything whose REAL path leaves `api_root`; refuses a missing file."""
    kind, fp, _ = classify_ref(file_part if ref is None else ref)
    if kind != LOCAL:
        raise ContractError("REFUSED: $ref %r is not a local file reference (only relative paths under the contract "
                            "directory are allowed; schemes, authorities and absolute paths are refused)." % (ref or file_part))
    target = os.path.realpath(os.path.abspath(os.path.join(base_dir, fp)))
    if not inside(target, api_root):
        raise ContractError("REFUSED: $ref %r resolves to %s, outside the contract root %s - a contract may only reference files "
                            "under its own domain's src/ directory." % (ref or file_part, target, os.path.realpath(api_root)))
    if not os.path.isfile(target):
        raise MissingDependency("REFUSED: $ref %r -> %s does not exist (referenced from %s) - add the file or fix the reference."
                                % (ref or file_part, target, base_dir))
    return target


def load_document(path):
    text = open(path, "rb").read().decode("utf-8-sig")
    try:
        doc = json.loads(text) if path.lower().endswith(".json") else yaml.safe_load(text)
    except Exception as e:  # noqa: BLE001 - any parse failure is a contract error
        raise ContractError("REFUSED: %s cannot be parsed: %s" % (path, str(e).splitlines()[0]))
    if not isinstance(doc, dict):
        raise ContractError("REFUSED: %s is not a mapping document" % path)
    return doc


def _refs(node, acc=None):
    acc = [] if acc is None else acc
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            acc.append(node["$ref"])
        for v in node.values():
            _refs(v, acc)
    elif isinstance(node, list):
        for v in node:
            _refs(v, acc)
    return acc


def _has_key(node, key):
    if isinstance(node, dict):
        return key in node or any(_has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_has_key(v, key) for v in node)
    return False


def _pointer(doc, fragment, ref, path):
    target = doc
    for part in [p for p in fragment.split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(target, list) and part.isdigit() and int(part) < len(target):
            target = target[int(part)]
        elif isinstance(target, dict) and part in target:
            target = target[part]
        else:
            raise ContractError("REFUSED: $ref %r - fragment %r not found in %s (missing %r)." % (ref, "#" + fragment, path, part))
    return target


def _rel(path, api_root):
    return os.path.relpath(os.path.realpath(path), os.path.realpath(api_root)).replace(os.sep, "/")


def component_name(rel_posix, fragment):
    """Deterministic lifted-component name: file stem (path separators -> _) + pointer segments.
    schemas/product.yaml#/components/schemas/Product -> schemas_product_components_schemas_Product"""
    head, _, tail = rel_posix.rpartition("/")
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    parts = ([head] if head else []) + [stem] + [p.replace("~1", "/").replace("~0", "~") for p in fragment.split("/") if p]
    return _COMPONENT_KEY.sub("_", "_".join(parts).replace("/", "_"))


# ── discovery ──────────────────────────────────────────────────────────────────

class _Graph:
    def __init__(self, root_path, api_root):
        self.root = os.path.realpath(os.path.abspath(root_path))
        self.api_root = os.path.realpath(os.path.abspath(api_root)) if api_root else contract_root(self.root)
        if not inside(self.root, self.api_root):
            raise ContractError("REFUSED: root contract %s is not under the contract root %s" % (self.root, self.api_root))
        if not os.path.isfile(self.root):
            raise MissingDependency("REFUSED: root contract %s does not exist" % self.root)
        self.docs, self.edges = {}, []

    def load(self, path):
        if path not in self.docs:
            self.docs[path] = load_document(path)
        return self.docs[path]

    def target_of(self, ref, cur_file):
        kind, fp, frag = classify_ref(ref)
        if kind == INTERNAL:
            return cur_file, frag
        if kind == UNSAFE:
            raise ContractError("REFUSED: unsafe $ref %r in %s - only relative paths under the contract directory are allowed "
                                "(no absolute paths, drive letters, file://, http:// or https://)." % (ref, cur_file))
        target = resolve_local(fp, os.path.dirname(cur_file), self.api_root, ref=ref)
        self.edges.append((_rel(cur_file, self.api_root), ref, _rel(target, self.api_root)))
        return target, frag

    def discover(self):
        pending, seen = [self.root], set()
        while pending:
            cur = pending.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            for ref in _refs(self.load(cur)):
                target, _ = self.target_of(ref, cur)
                if target not in seen:
                    pending.append(target)
        return {"root": _rel(self.root, self.api_root), "api_root": self.api_root,
                "files": sorted(_rel(p, self.api_root) for p in seen), "edges": list(self.edges)}


def discover_contract(root_path, api_root=None):
    """{'root', 'api_root', 'files' (sorted, repo-relative to api_root), 'edges' [(from, ref, to)]}.
    Follows the FILE portion of every $ref in every reachable file; internal refs add no file.
    Fails closed on unsafe, escaping or missing references."""
    return _Graph(root_path, api_root).discover()


# ── bundling: context-aware lifting ────────────────────────────────────────────
#
# The destination components/<section> of an external target is decided by the SEMANTIC
# CONTEXT of the reference in the OpenAPI structure - never by inspecting the target's
# content. `schema: {$ref}` is a Schema, `parameters: [{$ref}]` a Parameter,
# `responses.<code>: {$ref}` a Response, `headers.<name>: {$ref}` a Header, ... A reference in
# a context whose object type is not known FAILS CLOSED; an external PathItem reference
# FAILS CLOSED (no 3.0.3-compatible representation). The file portion of a reference feeds
# the dependency graph, the fragment selects the target, the context selects the section.

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}

# liftable semantic contexts == components sections of OAS 3.0
SCHEMA, RESPONSE, PARAMETER, EXAMPLE, REQUEST_BODY, HEADER, SECURITY_SCHEME, LINK, CALLBACK = (
    "schemas", "responses", "parameters", "examples", "requestBodies", "headers", "securitySchemes", "links", "callbacks")
LIFTABLE = (SCHEMA, RESPONSE, PARAMETER, EXAMPLE, REQUEST_BODY, HEADER, SECURITY_SCHEME, LINK, CALLBACK)
_TYPE_NAME = {SCHEMA: "Schema", RESPONSE: "Response", PARAMETER: "Parameter", EXAMPLE: "Example", REQUEST_BODY: "Request Body",
              HEADER: "Header", SECURITY_SCHEME: "Security Scheme", LINK: "Link", CALLBACK: "Callback"}
# structural contexts (never lifted)
ROOT, PATHS, PATH_ITEM, OPERATION, COMPONENTS, RESPONSES_MAP, HEADERS_MAP, CONTENT_MAP, MEDIA_TYPE, ENCODING_MAP, ENCODING = (
    "root", "paths", "pathItem", "operation", "components", "responsesMap", "headersMap", "contentMap", "mediaType", "encodingMap", "encoding")
EXAMPLES_MAP, LINKS_MAP, CALLBACKS_MAP, SCHEMAS_MAP, PARAMETERS_MAP, REQUEST_BODIES_MAP, SECURITY_SCHEMES_MAP, PATH_ITEMS_MAP, OPAQUE = (
    "examplesMap", "linksMap", "callbacksMap", "schemasMap", "parametersMap", "requestBodiesMap", "securitySchemesMap", "pathItemsMap", "opaque")

_MAP_ELEMENT = {PATHS: PATH_ITEM, RESPONSES_MAP: RESPONSE, HEADERS_MAP: HEADER, CONTENT_MAP: MEDIA_TYPE, ENCODING_MAP: ENCODING,
                EXAMPLES_MAP: EXAMPLE, LINKS_MAP: LINK, CALLBACKS_MAP: CALLBACK, SCHEMAS_MAP: SCHEMA, PARAMETERS_MAP: PARAMETER,
                REQUEST_BODIES_MAP: REQUEST_BODY, SECURITY_SCHEMES_MAP: SECURITY_SCHEME, PATH_ITEMS_MAP: PATH_ITEM, CALLBACK: PATH_ITEM}
_KEYED = {
    ROOT: {"paths": PATHS, "webhooks": PATHS, "components": COMPONENTS},
    PATH_ITEM: {"parameters": PARAMETER},  # + HTTP methods -> OPERATION (below)
    OPERATION: {"parameters": PARAMETER, "requestBody": REQUEST_BODY, "responses": RESPONSES_MAP, "callbacks": CALLBACKS_MAP},
    PARAMETER: {"schema": SCHEMA, "content": CONTENT_MAP, "examples": EXAMPLES_MAP},
    HEADER: {"schema": SCHEMA, "content": CONTENT_MAP, "examples": EXAMPLES_MAP},
    RESPONSE: {"headers": HEADERS_MAP, "content": CONTENT_MAP, "links": LINKS_MAP},
    MEDIA_TYPE: {"schema": SCHEMA, "examples": EXAMPLES_MAP, "encoding": ENCODING_MAP},
    ENCODING: {"headers": HEADERS_MAP},
    REQUEST_BODY: {"content": CONTENT_MAP},
    COMPONENTS: {"schemas": SCHEMAS_MAP, "responses": RESPONSES_MAP, "parameters": PARAMETERS_MAP, "examples": EXAMPLES_MAP,
                 "requestBodies": REQUEST_BODIES_MAP, "headers": HEADERS_MAP, "securitySchemes": SECURITY_SCHEMES_MAP, "links": LINKS_MAP,
                 "callbacks": CALLBACKS_MAP, "pathItems": PATH_ITEMS_MAP},
}
# sanity of a lifted target for its context - REQUIRED fields of the OAS object (validation,
# not type inference: the context already decided the type; a target that cannot be that type is refused)
_REQUIRED = {PARAMETER: ("name", "in"), RESPONSE: ("description",), REQUEST_BODY: ("content",), SECURITY_SCHEME: ("type",)}
_FORBIDDEN = {HEADER: ("in", "name"), SCHEMA: ("in", "content", "openIdConnectUrl")}


def child_context(ctx, key):
    """The semantic context of node[key] given the context of node (a string key, never a list index)."""
    if isinstance(key, str) and key.startswith("x-"):
        return OPAQUE
    if ctx in _MAP_ELEMENT:
        return _MAP_ELEMENT[ctx]
    if ctx == PATH_ITEM and key in HTTP_METHODS:
        return OPERATION
    if ctx == SCHEMA:
        if key in _MAP_OF_SCHEMAS or key in _CONTAINERS:
            return SCHEMAS_MAP
        if key in _LIST_OF_SCHEMAS or key in _SINGLE_SCHEMA or key == "items":
            return SCHEMA
        return OPAQUE
    return _KEYED.get(ctx, {}).get(key, OPAQUE)


class _Bundler(_Graph):
    def __init__(self, root_path, api_root):
        super().__init__(root_path, api_root)
        self.lifted, self.by_target, self.content, self.order = {}, {}, {}, []  # (section,name)->(path,frag); (path,frag)->section; (section,name)->content; [(section,name)]

    def check_id_policy(self, path, doc):
        if _has_key(doc, "$id") and any(classify_ref(r)[0] == LOCAL for r in _refs(doc)):
            raise ContractError("REFUSED: %s declares $id AND contains relative external $refs. JSON Schema resolves such references "
                                "against the $id base URI, which is outside the supported subset (references resolve against the "
                                "physical file). Remove the $id or make the references internal." % path)

    # ── the walk ────────────────────────────────────────────────────────────
    def walk(self, node, ctx, cur_file, where):
        if isinstance(node, list):
            return [self.walk(v, ctx, cur_file, "%s[%d]" % (where, i)) for i, v in enumerate(node)]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            return self.reference(node, ref, ctx, cur_file, where)
        lifting = cur_file != self.root
        if ctx == SCHEMA and lifting:
            return self.schema_policy(node, cur_file, where)
        return {k: self.walk(v, child_context(ctx, k), cur_file, "%s.%s" % (where, k)) for k, v in node.items()}

    def reference(self, node, ref, ctx, cur_file, where):
        kind, _, frag = classify_ref(ref)
        in_root = cur_file == self.root
        if kind == INTERNAL and in_root:  # authored same-document reference: untouched (siblings walked)
            return {k: (v if k == "$ref" else self.walk(v, child_context(ctx, k), cur_file, "%s.%s" % (where, k))) for k, v in node.items()}
        if ctx == PATH_ITEM:
            raise ContractError("REFUSED: %s: $ref %r is a PathItem reference. External (or lifted) PathItem references have no "
                                "OAS 3.0.3-compatible components representation and are outside the supported bundling subset - "
                                "inline the path item in the root contract." % (where, ref))
        if ctx not in LIFTABLE:
            raise ContractError("REFUSED: %s: $ref %r sits in a context whose OpenAPI object type cannot be determined (%s). The bundler "
                                "never guesses a target's type from its content; move the reference to a recognised location "
                                "(schema, parameters, responses, requestBody, headers, examples, links, callbacks, securitySchemes)."
                                % (where, ref, ctx))
        target, frag = self.target_of(ref, cur_file)
        name = self.lift(target, frag, ref, ctx, where)
        rest = {k: self.walk(v, child_context(ctx, k), cur_file, "%s.%s" % (where, k)) for k, v in node.items() if k != "$ref"}
        return {**rest, "$ref": "#/components/%s/%s" % (ctx, name)}

    # generic schema-object compatibility policy - Schema context of LIFTED content only ----
    def schema_policy(self, node, cur_file, where):
        out = {}
        for key, value in node.items():
            here = "%s.%s" % (where, key)
            if key in _REF_SEMANTICS:
                raise ContractError("REFUSED: %s uses %r, which changes reference semantics and cannot be preserved when the schema is "
                                    "lifted into the generated OpenAPI document." % (here, key))
            if key in _CONTAINERS:
                continue  # members are lifted individually when referenced; the container itself is never a Schema Object field
            if key in _MAP_OF_SCHEMAS:  # property NAMES are data: only the values are schemas
                out[key] = {n: self.walk(s, SCHEMA, cur_file, "%s.%s" % (here, n)) for n, s in value.items()} if isinstance(value, dict) else value
            elif key in _LIST_OF_SCHEMAS or (key == "items" and isinstance(value, list)):
                out[key] = self.walk(value, SCHEMA, cur_file, here) if isinstance(value, list) else value
            elif key in _SINGLE_SCHEMA or key == "items":
                out[key] = self.walk(value, SCHEMA, cur_file, here) if isinstance(value, dict) else value
            elif key in _VALUE_KEYWORDS or key.startswith("x-"):
                out[key] = value
            else:  # identifiers ($schema/$id/$comment) and any unknown non-keyword key: preserved as a vendor extension
                ext = "x-" + key.lstrip("$")
                if ext in node or ext in out:
                    raise ContractError("REFUSED: %s cannot be relocated to %r - that extension already exists." % (here, ext))
                out[ext] = value
        return out

    # ── lifting ─────────────────────────────────────────────────────────────
    def lift(self, path, fragment, ref, section, where):
        rel = _rel(path, self.api_root)
        name = component_name(rel, fragment)
        key, target_key = (section, name), (path, fragment)
        if target_key in self.by_target:  # already lifted (or being lifted - cycles are fine)
            prev_section = self.by_target[target_key]
            if prev_section != section:
                raise ContractError("REFUSED: %s: external target %s#%s is referenced as a %s here but was already lifted as a %s "
                                    "(components/%s) - one target has one OpenAPI object type; reference it consistently or split it."
                                    % (where, rel, fragment, _TYPE_NAME[section], _TYPE_NAME[prev_section], prev_section))
            return name
        if key in self.lifted:  # same section, same generated name, DIFFERENT target
            prev_path, prev_frag = self.lifted[key]
            raise ContractError("REFUSED: lifted component name %r (components/%s) derives from both %s#%s and %s#%s - rename one "
                                "target so the generated names stay unique." % (name, section, _rel(prev_path, self.api_root), prev_frag, rel, fragment))
        self.lifted[key], self.by_target[target_key] = target_key, section  # reserve first: cycle-safe, first-encounter order
        self.order.append(key)
        doc = self.load(path)
        self.check_id_policy(rel, doc)
        target = _pointer(doc, fragment, ref, rel) if fragment else doc
        if not isinstance(target, dict):
            raise ContractError("REFUSED: $ref %r targets a %s, not a %s Object." % (ref, type(target).__name__, _TYPE_NAME[section]))
        for field in _REQUIRED.get(section, ()):
            if field not in target:
                raise ContractError("REFUSED: %s: $ref %r is used as a %s Object but the target %s#%s has no required %r field - the "
                                    "target is not a valid %s." % (where, ref, _TYPE_NAME[section], rel, fragment, field, _TYPE_NAME[section]))
        for field in _FORBIDDEN.get(section, ()):
            if field in target:
                raise ContractError("REFUSED: %s: $ref %r is used as a %s Object but the target %s#%s carries %r, which a %s Object "
                                    "never has - the reference sits in the wrong context or targets the wrong object."
                                    % (where, ref, _TYPE_NAME[section], rel, fragment, field, _TYPE_NAME[section]))
        self.content[key] = self.walk(copy.deepcopy(target), section, path, "%s#%s" % (rel, fragment))
        return name

    def run(self):
        root = self.load(self.root)
        out = self.walk(root, ROOT, self.root, "$")
        if self.order:
            components = out.setdefault("components", {})
            if not isinstance(components, dict):
                raise ContractError("REFUSED: components is not a mapping in %s" % self.root)
            for section, name in self.order:
                target_map = components.setdefault(section, {})
                if not isinstance(target_map, dict):
                    raise ContractError("REFUSED: components.%s is not a mapping in %s" % (section, self.root))
                if name in target_map:
                    raise ContractError("REFUSED: generated component %r collides with an authored components.%s entry in %s - rename "
                                        "one of them." % (name, section, _rel(self.root, self.api_root)))
                target_map[name] = self.content[(section, name)]
        return out, {"root": _rel(self.root, self.api_root), "api_root": self.api_root,
                     "files": sorted(_rel(p, self.api_root) for p in self.docs), "edges": list(self.edges),
                     "lifted": ["%s/%s" % (s, n) for s, n in self.order]}


def bundle(root_path, api_root=None):
    """(document, contract). The self-contained OpenAPI document (a NEW structure; the source
    documents are never mutated) plus the discovery record ({'files', 'edges', 'lifted', ...};
    'lifted' lists '<section>/<name>' in first-encounter order). A contract with no external
    references bundles to a document equal to the root as loaded."""
    return _Bundler(root_path, api_root).run()


def external_refs(doc):
    """Every non-internal $ref string in a document (empty for a bundled document)."""
    return [r for r in _refs(doc) if classify_ref(r)[0] != INTERNAL]
