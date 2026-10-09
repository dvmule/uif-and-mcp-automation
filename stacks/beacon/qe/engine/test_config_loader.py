from pathlib import Path

import pytest

from dynamic_mapping.config_loader import load_config, parse_config
from dynamic_mapping.errors import ConfigError

EXAMPLE = Path(__file__).resolve().parents[2] / "src" / "schemas" / "examples" / "customer_orders.json"


def test_load_example_config():
    config = load_config(str(EXAMPLE))
    assert config.version == "1.0"
    assert config.source_names() == ("customer", "orders")
    assert len(config.mappings) == 6
    assert config.mappings[0].type == "one_to_one"
    assert config.mappings[2].targets == ("address_line1", "address_line2")
    assert config.rejected_path == "s3://bucket/rejected/${batch_id}/"
    assert len(config.output_targets) == 2
    assert config.policies.on_empty_input == "fail"


def test_defaults():
    config = parse_config({
        "version": "1.0",
        "sources": [{"name": "a", "path": "/tmp/a", "format": "parquet"}],
        "mappings": [{"type": "literal", "target": "x", "value": 1}],
        "output": {"targets": [{"format": "json", "path": "/tmp/out"}]},
    })
    assert config.joins == ()
    assert config.validations == ()
    assert config.output_targets[0].mode == "overwrite"
    assert config.policies.on_empty_input == "fail"


def test_unsupported_version_rejected():
    with pytest.raises(ConfigError, match="version"):
        parse_config({"version": "9.9", "sources": [], "mappings": []})


def test_missing_required_key_rejected():
    with pytest.raises(ConfigError, match="missing required key"):
        parse_config({
            "version": "1.0",
            "sources": [{"name": "a", "format": "parquet"}],  # no path
            "mappings": [],
        })


def test_join_condition_optional_for_cross():
    # UIFPE-167: a cross join carries no condition — a missing key and an
    # explicit null both parse to "" (validate_structure decides whether the
    # join TYPE needs one); before this a cross join could not even parse
    for j in [{"left": "a", "right": "b", "type": "cross"},
              {"left": "a", "right": "b", "type": "cross", "condition": None}]:
        config = parse_config({
            "version": "1.0",
            "sources": [{"name": "a", "path": "/a", "format": "parquet"},
                        {"name": "b", "path": "/b", "format": "parquet"}],
            "joins": [j],
            "mappings": [{"type": "one_to_one", "source": "a.x", "target": "x"}],
            "output": {"targets": [{"format": "json", "path": "/out"}]},
        })
        assert config.joins[0].condition == ""
        assert config.joins[0].type == "cross"


def test_invalid_json_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(str(bad))


# ── filters block (D18) ──────────────────────────────────────────────────────

def test_filters_parsed(tmp_path):
    import json
    from dynamic_mapping.config_loader import parse_config
    cfg = parse_config({
        "version": "1.0",
        "sources": [{"name": "s", "path": "/x", "format": "csv"}],
        "mappings": [{"type": "one_to_one", "source": "s.a", "target": "a"}],
        "filters": ["s.a > 0", "s.b = 1 OR s.c = 2"],
        "output": {"targets": [{"format": "parquet", "path": "/out"}]},
    })
    assert cfg.filters == ("s.a > 0", "s.b = 1 OR s.c = 2")


def test_filters_default_empty():
    from dynamic_mapping.config_loader import parse_config
    cfg = parse_config({
        "version": "1.0",
        "sources": [{"name": "s", "path": "/x", "format": "csv"}],
        "mappings": [{"type": "one_to_one", "source": "s.a", "target": "a"}],
        "output": {"targets": [{"format": "parquet", "path": "/out"}]},
    })
    assert cfg.filters == ()


def test_filters_must_be_string_list():
    import pytest
    from dynamic_mapping.config_loader import parse_config
    from dynamic_mapping.errors import ConfigError
    with pytest.raises(ConfigError, match="filters"):
        parse_config({
            "version": "1.0",
            "sources": [{"name": "s", "path": "/x", "format": "csv"}],
            "mappings": [{"type": "one_to_one", "source": "s.a", "target": "a"}],
            "filters": [{"condition": "s.a > 0"}],
            "output": {"targets": [{"format": "parquet", "path": "/out"}]},
        })
