"""In-memory store for Delivery Lab service registrations, CDC metrics, and poller watermarks.

Thread-safe via a threading.Lock. State is per-process — a durable store
(DynamoDB / Redis) would be needed to run more than one instance.
"""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.schemas.delivery_lab import (
    CdcConfig,
    MetricsBatch,
    PipelineMetricsResponse,
    PollerTableState,
    ServiceRegistration,
    StageMetrics,
)

_lock = threading.Lock()
_services: dict[str, dict] = {}
_table_totals: dict[str, dict] = {}
_watermark_state: dict[str, dict] = {}


def register_service(reg: ServiceRegistration) -> None:
    with _lock:
        _services[reg.service_id] = {
            **reg.model_dump(),
            "registered_at": datetime.now(UTC).isoformat(),
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }


def push_metrics(batch: MetricsBatch) -> None:
    with _lock:
        prev = _table_totals.get(batch.table_id, {"records": 0, "bytes": 0})
        _table_totals[batch.table_id] = {
            "records": prev["records"] + batch.records_written,
            "bytes": prev["bytes"] + batch.bytes_written,
            "last_batch_at": batch.polled_at.isoformat(),
        }
        if batch.service_id in _services:
            _services[batch.service_id]["last_heartbeat"] = datetime.now(UTC).isoformat()
        if batch.new_watermark:
            _watermark_state[batch.table_id] = {
                "table_id": batch.table_id,
                "last_watermark": batch.new_watermark,
                "last_run_at": batch.polled_at.isoformat(),
                "last_run_status": "success",
                "last_row_count": batch.records_written,
                "error_message": None,
            }


def get_pipeline_metrics() -> PipelineMetricsResponse:
    with _lock:
        total_records = sum(v["records"] for v in _table_totals.values())
        total_bytes = sum(v["bytes"] for v in _table_totals.values())
        last_at_str = max(
            (v["last_batch_at"] for v in _table_totals.values()),
            default=None,
        )
        return PipelineMetricsResponse(
            cdc=StageMetrics(
                records=total_records,
                bytes=total_bytes,
                last_batch_at=datetime.fromisoformat(last_at_str) if last_at_str else None,
            ),
            per_table={
                k: StageMetrics(
                    records=v["records"],
                    bytes=v["bytes"],
                    last_batch_at=datetime.fromisoformat(v["last_batch_at"]),
                )
                for k, v in _table_totals.items()
            },
            registered_services=list(_services.values()),
        )


def get_table_state(table_id: str) -> PollerTableState | None:
    with _lock:
        s = _watermark_state.get(table_id)
        return PollerTableState(**s) if s else None


def get_all_table_states() -> list[PollerTableState]:
    with _lock:
        return [PollerTableState(**s) for s in _watermark_state.values()]


def reset_watermark(table_id: str, watermark: str) -> None:
    with _lock:
        existing = _watermark_state.get(table_id, {})
        _watermark_state[table_id] = {
            "table_id": table_id,
            "last_watermark": watermark,
            "last_run_at": existing.get("last_run_at"),
            "last_run_status": existing.get("last_run_status"),
            "last_row_count": existing.get("last_row_count", 0),
            "error_message": None,
        }


def clear_watermark(table_id: str) -> None:
    with _lock:
        _watermark_state.pop(table_id, None)


def get_table_config(ssm_parameter: str = "", file_path: str = "") -> CdcConfig:
    if ssm_parameter:
        from app.clients.ssm_client import SsmClient
        return CdcConfig.model_validate(SsmClient().get_json(ssm_parameter))
    with open(file_path) as f:
        return CdcConfig(**json.load(f))


def put_table_config(config: CdcConfig, ssm_parameter: str = "", file_path: str = "") -> None:
    if ssm_parameter:
        from app.clients.ssm_client import SsmClient
        SsmClient().put_json(ssm_parameter, config.model_dump(by_alias=True))
        return
    Path(file_path).write_text(
        json.dumps(config.model_dump(by_alias=True), indent=2),
        encoding="utf-8",
    )
