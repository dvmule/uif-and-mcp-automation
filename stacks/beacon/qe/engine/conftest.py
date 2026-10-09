import os
import sys
import time

import pytest


@pytest.fixture(scope="session")
def spark():
    pyspark = pytest.importorskip("pyspark")
    from pyspark.sql import SparkSession

    # Executors default to launching "python3", which doesn't exist on Windows.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

    builder = (
        SparkSession.builder
        .master("local[2]")
        .appName("dynamic-mapping-tests")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
    )
    # On Windows the py4j gateway handshake can hit a transient PermissionError:
    # the conn-info file is still write-locked (JVM/antivirus) when PySpark reads
    # it. Retry the launch a few times before giving up.
    last_error = None
    for attempt in range(3):
        try:
            session = builder.getOrCreate()
            break
        except PermissionError as e:
            last_error = e
            time.sleep(5)
    else:
        raise last_error
    yield session
    session.stop()


@pytest.fixture()
def customer_orders_frames(spark):
    """In-memory equivalents of the example config's two sources."""
    customer = spark.createDataFrame(
        [
            (1, "Ada", "Lovelace", "1 Analytical Way, London"),
            (2, "Grace", "Hopper", "2 Compiler St, Arlington"),
            (3, "Alan", "Turing", "3 Enigma Rd, Bletchley"),
        ],
        ["customer_id", "first_name", "last_name", "full_address"],
    ).alias("customer")

    orders = spark.createDataFrame(
        [
            ("o-100", 1, 100.0, 20.0),
            ("o-101", 2, 50.0, 10.0),
            ("o-102", 2, -75.0, 0.0),   # fails gross_amount_positive
            (None,    3, 10.0, 2.0),    # fails order_id_not_null
        ],
        ["order_id", "customer_id", "order_total", "tax"],
    ).alias("orders")

    return {"customer": customer, "orders": orders}
