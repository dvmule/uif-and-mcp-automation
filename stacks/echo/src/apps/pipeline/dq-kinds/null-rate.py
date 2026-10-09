# null_rate.py
# Null-rate DQ kind — count nulls in a Silver/Gold column, fail if the ratio
# exceeds parameters.threshold_pct (0.0–1.0). Reads via Iceberg Spark SQL —
# no JDBC, no Oracle access.
import logging

logger = logging.getLogger(__name__)


def run_null_rate(spark, config):
    """Run a null-rate DQ check.

    Returns { status, null_ratio, total_rows, null_rows, message }.
    """
    params  = config.get("parameters", {})
    subject = config.get("subject", {})
    column  = params.get("column")
    threshold_pct = params.get("threshold_pct")

    if not column:
        return {"status": "FAILED", "null_ratio": None, "total_rows": 0, "null_rows": 0,
                "message": "parameters.column is required for null-rate kind"}
    if threshold_pct is None:
        return {"status": "FAILED", "null_ratio": None, "total_rows": 0, "null_rows": 0,
                "message": "parameters.threshold_pct is required (float 0-1)"}

    database = subject.get("database")
    table    = subject.get("table")
    table_full = f"glue_catalog.`{database}`.`{table}`"

    total_row = spark.sql(f"SELECT COUNT(*) AS c FROM {table_full}").first()
    total_rows = int(total_row[0] if total_row is not None else 0)

    if total_rows == 0:
        return {"status": "SKIPPED", "null_ratio": 0.0, "total_rows": 0, "null_rows": 0,
                "message": "table is empty — no null-rate signal to report"}

    null_row = spark.sql(
        f"SELECT COUNT(*) AS c FROM {table_full} WHERE `{column}` IS NULL"
    ).first()
    null_rows = int(null_row[0] if null_row is not None else 0)

    ratio = null_rows / total_rows if total_rows else 0.0
    threshold = float(threshold_pct)

    if ratio > threshold:
        return {
            "status": "FAILED",
            "null_ratio": ratio,
            "total_rows": total_rows,
            "null_rows":  null_rows,
            "message": (f"null ratio {ratio:.4f} for `{column}` exceeds threshold "
                        f"{threshold:.4f} ({null_rows}/{total_rows} rows null)"),
        }

    return {
        "status": "SUCCEEDED",
        "null_ratio": ratio,
        "total_rows": total_rows,
        "null_rows":  null_rows,
        "message": f"null ratio {ratio:.4f} within threshold {threshold:.4f}",
    }
