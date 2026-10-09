"""Pydantic shapes for the Delivery Lab endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field


class ServiceRegistration(BaseModel):
    service_id: str
    name: str
    role: str
    container_image: str
    produces: list[str]
    consumes: list[str]


class MetricsBatch(BaseModel):
    service_id: str
    stage: str
    table_id: str
    records_written: int
    bytes_written: int
    polled_at: datetime
    new_watermark: str | None = None


class TableConfig(BaseModel):
    model_config = {"extra": "ignore", "populate_by_name": True}

    id: str
    display_name: str = ""
    schema_name: str = Field("", alias="schema")
    table: str = ""
    watermark_col: str = ""
    poll_interval_seconds: int = 30
    batch_size: int = 1000
    overlap_seconds: int = 5
    initial_lookback_hours: int = 0
    enabled: bool = True


class CdcConfig(BaseModel):
    model_config = {"extra": "ignore"}

    poll_interval_seconds: int = 30
    pool_min: int = 2
    pool_max: int = 20
    pool_timeout_seconds: int = 30
    tables: list[TableConfig] = []


class StageMetrics(BaseModel):
    records: int
    bytes: int
    last_batch_at: datetime | None = None


class PipelineMetricsResponse(BaseModel):
    cdc: StageMetrics
    per_table: dict[str, StageMetrics]
    registered_services: list[dict]


class PollerTableState(BaseModel):
    table_id: str
    last_watermark: str | None = None
    last_run_at: str | None = None
    last_run_status: str | None = None
    last_row_count: int = 0
    error_message: str | None = None


class WatermarkReset(BaseModel):
    watermark: str
