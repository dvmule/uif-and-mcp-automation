"""UIFPE-168 at the engine level: per-run source filters (the incremental
window) and high-water-mark tracking. Spark-backed, injected frames."""
import json
import os
import sys
from datetime import datetime

import pytest

pytest.importorskip("pyspark")

from dynamic_mapping.config_loader import parse_config
from dynamic_mapping.errors import ConfigValidationError, EmptyInputError
from dynamic_mapping.main import run

pytestmark = pytest.mark.spark

# same convention as test_engine.py: tests that WRITE output need Hadoop's
# winutils on Windows
needs_local_writes = pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("HADOOP_HOME"),
    reason="local file writes need winutils.exe (set HADOOP_HOME) on Windows",
)

T1 = datetime(2026, 8, 25, 6, 30, 0)
T2 = datetime(2026, 8, 26, 6, 30, 0, 500000)
T3 = datetime(2026, 8, 27, 6, 30, 0, 1)


def product_config(tmp_path):
    return parse_config({
        "version": "1.0",
        "sources": [{"name": "product", "path": "ignored", "format": "parquet"}],
        "joins": [],
        "mappings": [{"type": "one_to_one", "source": "product.ppid",
                      "target": "ppid"}],
        "validations": [],
        "output": {"targets": [{"format": "json", "mode": "overwrite",
                                "path": (tmp_path / "out").as_posix()}]},
    })


def product_frame(spark, rows=None):
    rows = rows if rows is not None else [
        ("A1", T1), ("A2", T2), ("A3", T3)]
    return spark.createDataFrame(rows, ["ppid", "_gold_updated_at"]).alias("product")


TRACK = {"source": "product", "column": "_gold_updated_at"}


@needs_local_writes
def test_source_filter_narrows_the_window(spark, tmp_path):
    report = run(spark, product_config(tmp_path), batch_id="b-1",
                 frames={"product": product_frame(spark)},
                 source_filters={"product":
                                 "_gold_updated_at > TIMESTAMP '2026-08-26 06:30:00.500000'"},
                 track_max=TRACK)
    assert report.status == "succeeded"
    # strictly greater: the row AT the watermark is already delivered
    assert report.output_rows == 1
    out = spark.read.json((tmp_path / "out").as_posix())
    assert [r["ppid"] for r in out.collect()] == ["A3"]


@needs_local_writes
def test_high_water_mark_is_the_max_over_the_window(spark, tmp_path):
    report = run(spark, product_config(tmp_path), batch_id="b-1",
                 frames={"product": product_frame(spark)},
                 track_max=TRACK)
    # full read (baseline): max over all rows, full-microsecond formatting so
    # lexicographic order == chronological order
    assert report.high_water_mark == "2026-08-27 06:30:00.000001"


@needs_local_writes
def test_empty_window_succeeds_with_no_high_water_mark(spark, tmp_path):
    # a no-change window is NOT an empty source: on_empty_input (default
    # "fail") must not fire, the run succeeds with zero rows, and the
    # watermark must not move (max of nothing)
    report = run(spark, product_config(tmp_path), batch_id="b-1",
                 frames={"product": product_frame(spark)},
                 source_filters={"product":
                                 "_gold_updated_at > TIMESTAMP '2026-08-28 00:00:00'"},
                 track_max=TRACK)
    assert report.status == "succeeded"
    assert report.output_rows == 0
    assert report.high_water_mark is None


def test_empty_source_still_hits_the_policy(spark, tmp_path):
    # source_filters apply AFTER the empty-input policy: a genuinely empty
    # source keeps today's semantics even on an incremental feed
    empty = spark.createDataFrame([], "ppid string, _gold_updated_at timestamp") \
                 .alias("product")
    with pytest.raises(EmptyInputError):
        run(spark, product_config(tmp_path), batch_id="b-1",
            frames={"product": empty},
            source_filters={"product": "_gold_updated_at > TIMESTAMP '2026-01-01 00:00:00'"},
            track_max=TRACK)


def test_unknown_track_source_or_column_fails_loudly(spark, tmp_path):
    frames = {"product": product_frame(spark)}
    with pytest.raises(ConfigValidationError):
        run(spark, product_config(tmp_path), batch_id="b-1", frames=frames,
            track_max={"source": "ghost", "column": "_gold_updated_at"})
    with pytest.raises(ConfigValidationError):
        run(spark, product_config(tmp_path), batch_id="b-1", frames=frames,
            track_max={"source": "product", "column": "_ghost_column"})


@needs_local_writes
def test_report_dict_carries_the_high_water_mark(spark, tmp_path):
    # the stage driver serializes to_dict() into report.json — the field the
    # success branch advances the watermark from must survive the round trip
    report = run(spark, product_config(tmp_path), batch_id="b-1",
                 frames={"product": product_frame(spark)}, track_max=TRACK)
    assert json.loads(json.dumps(report.to_dict()))["high_water_mark"] == \
        "2026-08-27 06:30:00.000001"
