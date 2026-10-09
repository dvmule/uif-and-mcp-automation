# reconcile.py
# Reconcile DQ kind — extracted verbatim from silver-reconcile.py's
# core anti-join + safety-cap + dry-run + MERGE-DELETE pipeline. Spark-side
# IO (Oracle JDBC read, Iceberg PK read) is injected via callables so unit
# tests can stub them without standing up Spark.
import json
import logging

logger = logging.getLogger(__name__)

# Same NLS init as the legacy reconcile job — keep dates/timezones consistent
# between extract phases.
_NLS_INIT = (
    "BEGIN "
    "EXECUTE IMMEDIATE 'ALTER SESSION SET NLS_DATE_FORMAT=''YYYY-MM-DD\"T\"HH24:MI:SS'''; "
    "EXECUTE IMMEDIATE 'ALTER SESSION SET TIME_ZONE=''UTC'''; "
    "END;"
)


def build_pk_conditions(primary_key_cols, alias_left="t", alias_right="o"):
    return " AND ".join([f"{alias_left}.`{c}` = {alias_right}.`{c}`" for c in primary_key_cols])


def _resolve_oracle_credentials(secret_arn):
    import boto3
    secrets = boto3.client("secretsmanager", region_name="eu-west-1")
    raw = secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    creds = json.loads(raw)
    dsn = creds.get("dsn") or "//{host}:{port}/{service}".format(**creds)
    return creds["username"], creds["password"], f"jdbc:oracle:thin:@{dsn}"


def _default_oracle_pk_loader(spark, *, jdbc_url, user, password, table, pk_cols,
                              partition_col=None, lower=1, upper=10_000_000, num_partitions=4,
                              fetch_size=10_000, read_timeout_ms=7_200_000):
    pk_col_list = ", ".join(pk_cols)
    oracle_sql = f"(SELECT {pk_col_list} FROM {table}) t"
    reader = (
        spark.read.format("jdbc")
        .option("url", jdbc_url)
        .option("dbtable", oracle_sql)
        .option("user", user)
        .option("password", password)
        .option("driver", "oracle.jdbc.OracleDriver")
        .option("fetchsize", str(fetch_size))
        .option("sessionInitStatement", _NLS_INIT)
        .option("oracle.net.CONNECT_TIMEOUT", "30000")
        .option("oracle.net.READ_TIMEOUT", str(read_timeout_ms))
        .option("oracle.jdbc.ReadTimeout", str(read_timeout_ms))
        .option("oracle.net.disableOob", "false")
        .option("oracle.jdbc.keepAlive", "true")
    )
    if partition_col:
        reader = (reader
                  .option("partitionColumn", partition_col)
                  .option("lowerBound", str(lower))
                  .option("upperBound", str(upper))
                  .option("numPartitions", str(num_partitions)))
    return reader.load()


def _default_iceberg_pk_loader(spark, *, database, table, pk_cols):
    pk_col_list = ", ".join(pk_cols)
    table_full = f"glue_catalog.`{database}`.`{table}`"
    return spark.sql(f"SELECT {pk_col_list} FROM {table_full}")


def run_reconcile(spark, config, *,
                  oracle_pk_loader=None,
                  iceberg_pk_loader=None,
                  oracle_table_override=None):
    """Run a reconcile DQ check.

    Returns { status, orphan_count, rows_deleted, message }.
    """
    params  = config.get("parameters", {})
    subject = config.get("subject", {})
    pk_cols = params.get("pk_cols") or []
    if not pk_cols:
        return {"status": "FAILED", "orphan_count": 0, "rows_deleted": 0,
                "message": "parameters.pk_cols is required for reconcile kind"}

    silver_db    = subject.get("database")
    silver_tbl   = subject.get("table")
    oracle_table = oracle_table_override or params.get("oracle_table") or silver_tbl
    secret_arn   = params.get("oracle_secret_arn")
    max_deletes  = int(params.get("max_deletes_per_run", 5000))
    dry_run      = bool(params.get("dry_run", True))
    partition_col = params.get("pk_partition_col")

    if oracle_pk_loader is None:
        user, password, jdbc_url = _resolve_oracle_credentials(secret_arn)
        oracle_pks = _default_oracle_pk_loader(
            spark, jdbc_url=jdbc_url, user=user, password=password,
            table=oracle_table, pk_cols=pk_cols, partition_col=partition_col,
        )
    else:
        oracle_pks = oracle_pk_loader(spark=spark, table=oracle_table, pk_cols=pk_cols)

    if iceberg_pk_loader is None:
        iceberg_pks = _default_iceberg_pk_loader(spark, database=silver_db, table=silver_tbl, pk_cols=pk_cols)
    else:
        iceberg_pks = iceberg_pk_loader(spark=spark, database=silver_db, table=silver_tbl, pk_cols=pk_cols)

    oracle_pks.createOrReplaceTempView("oracle_pks")
    iceberg_pks.createOrReplaceTempView("iceberg_pks")

    on_clause = build_pk_conditions(pk_cols, "i", "o")
    orphans = spark.sql(
        f"SELECT i.* FROM iceberg_pks i LEFT ANTI JOIN oracle_pks o ON {on_clause}"
    )
    orphan_count = orphans.count()

    if orphan_count > max_deletes:
        return {
            "status": "FAILED",
            "orphan_count": orphan_count,
            "rows_deleted": 0,
            "message": (f"Orphan count ({orphan_count}) exceeds safety cap "
                        f"({max_deletes}). Aborting — raise max_deletes_per_run if intentional."),
        }

    if dry_run:
        return {
            "status": "SKIPPED",
            "orphan_count": orphan_count,
            "rows_deleted": 0,
            "message": f"dry-run (orphans={orphan_count})",
        }

    if orphan_count == 0:
        return {
            "status": "SUCCEEDED",
            "orphan_count": 0,
            "rows_deleted": 0,
            "message": "no orphans",
        }

    orphans.createOrReplaceTempView("orphans")
    table_full = f"glue_catalog.`{silver_db}`.`{silver_tbl}`"
    pk_join = build_pk_conditions(pk_cols, "t", "o")
    spark.sql(
        f"MERGE INTO {table_full} t USING orphans o ON {pk_join} WHEN MATCHED THEN DELETE"
    )

    return {
        "status": "SUCCEEDED",
        "orphan_count": orphan_count,
        "rows_deleted": orphan_count,
        "message": f"deleted {orphan_count} orphan rows",
    }
