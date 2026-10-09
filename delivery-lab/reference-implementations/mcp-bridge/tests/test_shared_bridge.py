"""shared_bridge.py - the pure pieces of the shared-Bridge model: the shared identity
and effective config, the merge of one domain into the shared MCP asset content, and
the Exchange-resolved shared asset version. No network anywhere."""
import copy
import json
from pathlib import Path

import pytest
import yaml

import shared_bridge as sb
from generate_mcp_metadata import generate

REPO = Path(__file__).resolve().parents[4]
SRC = Path(__file__).resolve().parents[1] / "scripts"
from anypoint_fake import PRODUCT_SPEC  # test-owned single-file fixture contract
PRODUCT_CFG = REPO / "domains" / "product" / "src" / "mcp" / "bridge-config.yaml"
ORG = "8c27297e-4bcf-4c53-99eb-ffd7b1069294"

PLATFORM_DEV = {"org_id": ORG, "env_id": "e", "shared_bridge": {"instance_label": "uif-domains-mcp", "mcp_asset_id": "uif-domains-mcp",
                                                                "base_path": "/domainsTest/", "transcoding_path": "/mcp"}}


def _meta(spec_path, asset, version, tools_rename=None):
    meta = generate(str(spec_path), source_ref={"assetId": asset, "version": version, "groupId": ORG},
                    mcp_asset={"assetId": "uif-domains-mcp", "version": "x"}, org_id=ORG)["metadata"]
    for t in meta["tools"]:
        if tools_rename:
            t["name"] = tools_rename(t["name"])
    return meta


@pytest.fixture(scope="module")
def product_meta():
    return _meta(PRODUCT_SPEC, "product-api", "1.0.0")


@pytest.fixture(scope="module")
def stock_meta():
    # a second domain with its own tool names (derived the same way, different paths)
    return _meta(PRODUCT_SPEC, "stock-api", "1.0.0", tools_rename=lambda n: n.replace("products", "stock"))


# ── shared identity ─────────────────────────────────────────────────────────────

def test_repo_platform_config_declares_a_complete_shared_dev_bridge():
    """The repository's DEV block is complete and self-consistent (its concrete identities -
    real or temporary test isolation - are asserted in test_isolation.py)."""
    platform = yaml.safe_load((SRC / "anypoint-environments.yaml").read_text(encoding="utf-8"))
    shared = sb.load_shared(platform["dev"], "dev")
    assert shared["instance_label"].startswith("uif-domains-mcp") and shared["mcp_asset_id"] == shared["instance_label"]
    assert shared["base_path"] == "uif/domains" and shared["transcoding_path"] == "/mcp"  # confirmed listener path /uif/domains, normalized
    assert isinstance(shared["protected"], dict)


def test_load_shared_normalizes_and_defaults():
    shared = sb.load_shared(PLATFORM_DEV, "dev")
    assert shared["base_path"] == "domainsTest"  # slashes stripped
    no_tp = copy.deepcopy(PLATFORM_DEV)
    del no_tp["shared_bridge"]["transcoding_path"]
    assert sb.load_shared(no_tp, "dev")["transcoding_path"] == "/mcp"


def test_load_shared_fails_closed_when_absent_or_incomplete():
    with pytest.raises(SystemExit) as e:
        sb.load_shared({"org_id": ORG}, "dev")
    assert "shared_bridge" in str(e.value) and "never invented" in str(e.value)
    broken = copy.deepcopy(PLATFORM_DEV)
    del broken["shared_bridge"]["mcp_asset_id"]
    with pytest.raises(SystemExit) as e:
        sb.load_shared(broken, "dev")
    assert "lacks mcp_asset_id" in str(e.value)


def test_effective_config_adds_the_shared_identity_and_leaves_the_domain_config_alone():
    cfg = yaml.safe_load(PRODUCT_CFG.read_text(encoding="utf-8"))
    assert "mcp_asset_id" not in cfg and "base_path" not in cfg  # Product contributes a route, it owns no Bridge identity
    before = copy.deepcopy(cfg)
    eff = sb.effective_config(cfg, sb.load_shared(PLATFORM_DEV, "dev"))
    assert eff["mcp_asset_id"] == "uif-domains-mcp" and eff["transcoding_path"] == "/mcp"
    assert eff["domain"] == "product" and eff["source_asset_id"] == cfg["source_asset_id"]
    assert eff.get("environments") == cfg.get("environments")  # no common upstream in the test platform -> nothing injected, nothing invented
    assert eff.get("source_instance_name") == cfg.get("source_instance_name")  # domain-owned, passed through
    assert cfg == before


@pytest.mark.parametrize("extra, message", [
    ({"mcp_asset_id": "uif-product-mcp"}, "remove the per-domain value"),
    ({"base_path": "productTest"}, "a domain cannot choose one"),
    ({"transcoding_path": "/product-mcp"}, "one transport path per Bridge"),
])
def test_effective_config_refuses_per_domain_bridge_identity(extra, message):
    cfg = dict(yaml.safe_load(PRODUCT_CFG.read_text(encoding="utf-8")), **extra)
    with pytest.raises(SystemExit) as e:
        sb.effective_config(cfg, sb.load_shared(PLATFORM_DEV, "dev"))
    assert message in str(e.value)


def test_effective_config_defaults_the_rest_asset_to_domain_api_and_requires_a_domain():
    """Convention: a brand-new domain needs only `domain` + environments.<env>.upstream_url."""
    eff = sb.effective_config({"domain": "customer", "environments": {"dev": {"upstream_url": "https://customer/"}}}, sb.load_shared(PLATFORM_DEV, "dev"))
    assert eff["source_asset_id"] == "uif-customer-api" and eff["mcp_asset_id"] == "uif-domains-mcp"  # convention uif-<domain>-api
    eff = sb.effective_config({"domain": "customer", "source_asset_id": "customer-api-test"}, sb.load_shared(PLATFORM_DEV, "dev"))
    assert eff["source_asset_id"] == "customer-api-test"  # an explicit value always wins
    with pytest.raises(SystemExit) as e:
        sb.effective_config({"source_asset_id": "x-api"}, sb.load_shared(PLATFORM_DEV, "dev"))
    assert "no `domain:`" in str(e.value)


def test_effective_config_accepts_the_same_values_and_writes_a_build_artifact(tmp_path):
    cfg = dict(yaml.safe_load(PRODUCT_CFG.read_text(encoding="utf-8")), mcp_asset_id="uif-domains-mcp", transcoding_path="/mcp")
    eff = sb.effective_config(cfg, sb.load_shared(PLATFORM_DEV, "dev"))
    path = sb.write_effective_config(eff, str(tmp_path / "build"))
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert Path(path).name == "effective-bridge-config.yaml" and doc == eff
    assert "GENERATED by deploy_bridge.py" in Path(path).read_text(encoding="utf-8")


# ── the shared MCP asset content ─────────────────────────────────────────────────

def test_first_domain_alone_is_its_own_metadata(product_meta):
    merged = sb.merge_metadata(None, product_meta, [])
    assert merged == {"transport": product_meta["transport"], "tools": product_meta["tools"], "connections": product_meta["connections"]}
    assert merged is not product_meta and merged["tools"] is not product_meta["tools"]


def test_second_domain_is_added_and_the_first_preserved_verbatim(product_meta, stock_meta):
    merged = sb.merge_metadata(product_meta, stock_meta, [])
    assert sb.tool_names(merged) == sorted(sb.tool_names(product_meta) + sb.tool_names(stock_meta))
    assert merged["tools"][:len(product_meta["tools"])] == product_meta["tools"]  # Product's tools first, byte-for-byte
    assert sb.connection_versions(merged) == {"product-api": "1.0.0", "stock-api": "1.0.0"}
    assert merged["transport"] == product_meta["transport"]


def test_domain_update_replaces_only_its_own_tools_and_connection(product_meta, stock_meta):
    shared_now = sb.merge_metadata(product_meta, stock_meta, [])
    product_101 = _meta(PRODUCT_SPEC, "product-api", "1.0.1")
    product_101["tools"].append({"name": "get_probes_id", "description": "new in 1.0.1", "inputSchema": {"type": "object", "properties": {}}})
    merged = sb.merge_metadata(shared_now, product_101, sb.tool_names(product_meta))
    assert sb.connection_versions(merged) == {"stock-api": "1.0.0", "product-api": "1.0.1"}
    assert sb.tool_names(merged) == sorted(sb.tool_names(stock_meta) + sb.tool_names(product_101))
    stock_after = [t for t in merged["tools"] if t["name"] in sb.tool_names(stock_meta)]
    assert stock_after == stock_meta["tools"]  # Stock untouched
    assert len(merged["connections"]) == 2


def test_absent_domain_is_never_removed(product_meta, stock_meta):
    """Deploying Product against a shared asset that also carries Stock keeps Stock."""
    shared_now = sb.merge_metadata(product_meta, stock_meta, [])
    merged = sb.merge_metadata(shared_now, product_meta, sb.tool_names(product_meta))  # Product unchanged
    assert sb.tool_names(merged) == sb.tool_names(shared_now) and sb.connection_versions(merged) == sb.connection_versions(shared_now)
    assert sb.metadata_equal(sb.render_metadata(shared_now), sb.render_metadata(merged))[0]


def test_cross_domain_tool_name_collision_fails_closed(product_meta):
    other = copy.deepcopy(product_meta)  # another domain that derives the SAME tool names
    other["connections"][0]["name"] = other["connections"][0]["ref"]["assetId"] = "clone-api"
    with pytest.raises(SystemExit) as e:
        sb.merge_metadata(product_meta, other, [])  # no ownership record given -> every existing tool is another domain's
    assert "already served by ANOTHER domain" in str(e.value) and "get_products" in str(e.value)
    with pytest.raises(SystemExit) as e:
        sb.merge_metadata(product_meta, other, [], protected_tool_names=sb.tool_names(product_meta))  # Product's route claims them
    assert "already served by ANOTHER domain" in str(e.value)


def test_orphaned_tools_no_route_serves_are_replaced_not_treated_as_another_domains(product_meta):
    """A tool present in the asset but routed by nobody (an earlier drift) is not another
    domain's: this domain may re-claim the name; unrelated orphans are kept, never dropped."""
    stale = copy.deepcopy(product_meta)
    stale["tools"].append({"name": "get_forgotten", "description": "nobody routes this", "inputSchema": {"type": "object", "properties": {}}})
    # Product's route currently lists only get_products (post_products_query fell out of the mapping); Stock protects nothing here
    merged = sb.merge_metadata(stale, product_meta, ["get_products"], protected_tool_names=[])
    assert sb.tool_names(merged) == sorted(sb.tool_names(product_meta) + ["get_forgotten"])
    assert [t["name"] for t in merged["tools"]][:len(product_meta["tools"])] == [t["name"] for t in product_meta["tools"]]  # block stays in place


def test_transport_path_mismatch_fails_closed(product_meta, stock_meta):
    other = copy.deepcopy(stock_meta)
    other["transport"]["path"] = "/other"
    with pytest.raises(SystemExit) as e:
        sb.merge_metadata(product_meta, other, [])
    assert "one transport per Bridge" in str(e.value)


def test_domain_metadata_must_carry_exactly_one_connection(product_meta):
    bad = copy.deepcopy(product_meta)
    bad["connections"] = []
    with pytest.raises(SystemExit) as e:
        sb.merge_metadata(None, bad, [])
    assert "exactly ONE connection" in str(e.value)


def test_render_parse_roundtrip_and_equality(product_meta):
    raw = sb.render_metadata(product_meta)
    assert sb.parse_metadata(raw) == product_meta
    assert sb.metadata_equal(raw, raw) == (True, "byte-identical")
    reserialized = json.dumps(product_meta, separators=(",", ":")).encode()  # the UI/Exchange may re-serialize
    assert sb.metadata_equal(reserialized, raw) == (True, "same document (serialization differs)")
    assert sb.metadata_equal(raw, sb.render_metadata(dict(product_meta, tools=[])))[0] is False
    with pytest.raises(SystemExit):
        sb.parse_metadata(b"not json")
    with pytest.raises(SystemExit):
        sb.parse_metadata(b'{"no": "tools"}')


# ── the shared MCP asset version ────────────────────────────────────────────────

class FakeReader:
    def __init__(self, versions, latest=None):
        self.versions = dict(versions)  # version -> bytes
        self.latest = latest if latest is not None else (max(self.versions, key=lambda v: tuple(int(x) for x in v.split("."))) if self.versions else None)
        self.trace = []

    def latest_descriptor(self, group, asset):
        self.trace.append(("latest", asset))
        return None if self.latest is None else {"version": self.latest, "otherVersions": sorted(self.versions)}

    def descriptor(self, group, asset, version):
        self.trace.append(("descriptor", asset, version))
        return {"version": version, "files": [{"classifier": "mcp-metadata", "packaging": "json", "externalLink": "x"}]} if version in self.versions else None

    def published_file(self, descriptor, classifier, main_file):
        self.trace.append(("file", descriptor["version"]))
        return self.versions.get(descriptor["version"])


def test_reuse_when_merged_equals_the_bound_version(product_meta):
    raw = sb.render_metadata(product_meta)
    rel = sb.resolve_shared_version(FakeReader({"1.0.0": raw, "1.0.1": b"{}"}), ORG, "uif-domains-mcp", "1.0.0", raw)
    assert rel["decision"] == "reuse" and rel["version"] == "1.0.0" and rel["bound"] == "1.0.0"


def test_initial_when_the_asset_is_absent(product_meta):
    rel = sb.resolve_shared_version(FakeReader({}), ORG, "uif-domains-mcp", None, sb.render_metadata(product_meta))
    assert rel["decision"] == "initial" and rel["version"] == "1.0.0"


def test_bump_when_merged_differs_from_latest(product_meta, stock_meta):
    raw_prod = sb.render_metadata(product_meta)
    merged = sb.render_metadata(sb.merge_metadata(product_meta, stock_meta, []))
    rel = sb.resolve_shared_version(FakeReader({"1.0.0": raw_prod}), ORG, "uif-domains-mcp", "1.0.0", merged)
    assert rel["decision"] == "bump" and rel["version"] == "1.0.1" and rel["latest"] == "1.0.0"
    # a new Bridge (nothing bound) with an existing, different asset history also bumps
    rel = sb.resolve_shared_version(FakeReader({"1.0.0": raw_prod, "1.0.4": b'{"tools": []}'}), ORG, "uif-domains-mcp", None, merged)
    assert rel["decision"] == "bump" and rel["version"] == "1.0.5"


def test_rebind_to_an_unbound_latest_with_identical_content(product_meta, stock_meta):
    merged = sb.render_metadata(sb.merge_metadata(product_meta, stock_meta, []))
    reader = FakeReader({"1.0.0": sb.render_metadata(product_meta), "1.0.1": merged})
    rel = sb.resolve_shared_version(reader, ORG, "uif-domains-mcp", "1.0.0", merged)
    assert rel["decision"] == "reuse" and rel["version"] == "1.0.1"


def test_fail_closed_on_inconsistent_exchange_state(product_meta):
    raw = sb.render_metadata(product_meta)
    with pytest.raises(SystemExit) as e:  # the Bridge binds a version Exchange does not have
        sb.resolve_shared_version(FakeReader({"1.0.0": raw}), ORG, "uif-domains-mcp", "1.0.3", raw)
    assert "Exchange has no such version" in str(e.value)
    with pytest.raises(SystemExit) as e:  # next version already exists although latest is lower
        sb.resolve_shared_version(FakeReader({"1.0.0": raw, "1.0.1": b"{}"}, latest="1.0.0"), ORG, "uif-domains-mcp", None, b'{"tools":[]}')
    assert "ambiguous history" in str(e.value) or "non-linear" in str(e.value)
    with pytest.raises(SystemExit) as e:  # bound version has no downloadable file
        class NoFile(FakeReader):
            def published_file(self, d, c, m):
                return None
        sb.resolve_shared_version(NoFile({"1.0.0": raw}), ORG, "uif-domains-mcp", "1.0.0", raw)
    assert "no downloadable" in str(e.value)
    with pytest.raises(SystemExit):  # non-semver bound version
        sb.resolve_shared_version(FakeReader({"1.0.0": raw}), ORG, "uif-domains-mcp", "latest", raw)
