"""Delivery Lab endpoints — service registry, CDC metrics, table config, watermark state."""

from fastapi import APIRouter, HTTPException

import app.services.delivery_lab_service as svc
from app.core.config import get_settings
from app.schemas.delivery_lab import (
    CdcConfig,
    MetricsBatch,
    PipelineMetricsResponse,
    PollerTableState,
    ServiceRegistration,
    WatermarkReset,
)

router = APIRouter(prefix="/delivery-lab", tags=["delivery-lab"])


@router.post("/services/register", status_code=204)
async def register_service(reg: ServiceRegistration) -> None:
    svc.register_service(reg)


@router.post("/metrics", status_code=204)
async def push_metrics(batch: MetricsBatch) -> None:
    svc.push_metrics(batch)


@router.get("/pipeline", response_model=PipelineMetricsResponse)
async def get_pipeline() -> PipelineMetricsResponse:
    return svc.get_pipeline_metrics()


@router.get("/tables/state", response_model=list[PollerTableState])
async def get_all_table_states() -> list[PollerTableState]:
    return svc.get_all_table_states()


@router.get("/tables/{table_id}/state", response_model=PollerTableState)
async def get_table_state(table_id: str) -> PollerTableState:
    state = svc.get_table_state(table_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"No state found for table '{table_id}'")
    return state


@router.put("/tables/{table_id}/watermark", status_code=204)
async def reset_watermark(table_id: str, body: WatermarkReset) -> None:
    svc.reset_watermark(table_id, body.watermark)


@router.delete("/tables/{table_id}/watermark", status_code=204)
async def clear_watermark(table_id: str) -> None:
    svc.clear_watermark(table_id)


@router.get("/tables", response_model=CdcConfig)
async def get_tables() -> CdcConfig:
    cfg = get_settings()
    try:
        return svc.get_table_config(
            ssm_parameter=cfg.cdc_tables_ssm_parameter,
            file_path=cfg.cdc_tables_path,
        )
    except FileNotFoundError:
        # `from None`: a missing config file is the expected absent-state, not a
        # fault. The path came from our own settings and the 404 detail already
        # states the condition, so the original adds no diagnostic value.
        raise HTTPException(status_code=404, detail="CDC tables config not found") from None
    except RuntimeError as exc:
        # `from exc`: raised by the SSM read and carries the AWS-side reason,
        # which is exactly what the 502 detail reports. Keep the cause.
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/tables", status_code=204)
async def put_tables(config: CdcConfig) -> None:
    cfg = get_settings()
    try:
        svc.put_table_config(
            config,
            ssm_parameter=cfg.cdc_tables_ssm_parameter,
            file_path=cfg.cdc_tables_path,
        )
    except RuntimeError as exc:
        # `from exc`: the SSM write failure reason is the 502 detail. Diagnostic.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OSError as exc:
        # `from exc`: errno and filename are the only clue to a failed local
        # write, and they live on the original. Diagnostic.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
