# gold_fixtures.py — the fake Glue catalog the control-panel suite shares.
# Deliberately NOT a test module (same reason as lifecycle_fixtures.py): under
# pytest's importlib import mode test modules cannot import each other, and
# the gold-catalog fake is needed by the picker tests (test_gold_catalog),
# the validation tests (test_validation) and the joined-feed tests
# (test_joined_feeds). One fake, so "what the catalog says" cannot drift
# between the suites that assert on it.

# Glue raises a client error whose class name is EntityNotFoundException for
# a database that does not exist; registries.gold_tables matches on the
# NAME, so a plain Exception subclass with that name exercises the same path.
EntityNotFoundException = type("EntityNotFoundException", (Exception,), {})


def _glue_columns(spec):
    """Glue `Columns` entries for a fixture table: a list of names (every
    column typed string) or a {name: type} map — the row-filter builder reads
    the catalogued type, so the fake carries one like the real service."""
    items = spec.items() if isinstance(spec, dict) else ((c, "string") for c in spec)
    return [{"Name": name, "Type": typ} for name, typ in items]


class FakeGlue:
    """get_tables/get_table over an in-memory {db: {table: columns}} where
    columns is a name list or a {name: type} map (see _glue_columns).
    A database missing from the catalog raises EntityNotFoundException, as
    the real service does; fail=True raises a generic error (an outage)."""

    def __init__(self, catalog=None, fail=False):
        self.catalog = catalog or {}
        self.fail = fail

    def get_tables(self, DatabaseName, **kwargs):
        if self.fail:
            raise RuntimeError("glue down")
        if DatabaseName not in self.catalog:
            raise EntityNotFoundException(f"Database {DatabaseName} not found.")
        return {"TableList": [
            {"Name": name,
             "StorageDescriptor": {"Columns": _glue_columns(cols)}}
            for name, cols in self.catalog[DatabaseName].items()]}

    def get_table(self, DatabaseName, Name):
        if self.fail:
            raise RuntimeError("glue down")
        # a missing database OR table is EntityNotFoundException, as on the
        # real service (UIFPE-569 tells the two failure kinds apart by name)
        if DatabaseName not in self.catalog or Name not in self.catalog[DatabaseName]:
            raise EntityNotFoundException(f"Entity Not Found: {DatabaseName}.{Name}")
        cols = self.catalog[DatabaseName][Name]
        return {"Table": {"StorageDescriptor": {"Columns": _glue_columns(cols)}}}


# Column SETS are what the picker/validation suites assert on (counts, name
# lists); the types are for the columns route and the filter builder.
CATALOG = {
    "uif_echo_dev_gold_product": {
        "product": {"product_id": "bigint", "isbn13": "string",
                    "_gold_updated_at": "timestamp", "_gold_run_id": "string"},
        "product_changes": ["product_id", "change_type"],  # no CDC column
    },
    "uif_echo_dev_gold_program": {
        "program": ["program_id", "name", "_gold_updated_at"],
    },
    # UIFPE-167: a second domain to join onto product — the price entity's
    # own gold database (draft table, so no CDC column yet)
    "uif_echo_dev_gold_price": {
        "price": {"product_id": "bigint", "isbn13": "string",
                  "price_specifications": "array<struct<price:decimal(10,2),currency:string>>"},
    },
}

#: BEACON_GOLD_DATABASES value covering every database in CATALOG
GOLD_DBS = "uif_echo_dev_gold_product, uif_echo_dev_gold_program, uif_echo_dev_gold_price"
