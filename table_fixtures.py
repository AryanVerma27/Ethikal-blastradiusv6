"""Cache the conversion of static CSV fixture rows while building the image."""

import marshal
from pathlib import Path

CACHE = Path(__file__).with_name("fixture-tables")


def build(source):
    import sys

    sys.path.insert(0, str(source))
    from tests import test_executor as mod

    TestExecutor = mod.TestExecutor

    TestExecutor.setUpClass()
    try:
        CACHE.mkdir(exist_ok=True)
        for suite in ("tpch", "tpcds"):
            conn = mod.duckdb.connect(
                str(CACHE / (suite + ".duckdb")), config={"threads": 1}
            )
            folder, schema = (
                (mod.DIR_TPCH, mod.TPCH_SCHEMA)
                if suite == "tpch"
                else (mod.DIR_TPCDS, mod.TPCDS_SCHEMA)
            )
            try:
                for name, columns in schema.items():
                    conn.execute(
                        f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM READ_CSV('{folder}{name}.csv.gz', delim='|', header=True, columns={columns})"
                    )
            finally:
                conn.close()
            for name, table in getattr(TestExecutor, suite + "_tables").items():
                (CACHE / (suite + "-" + name)).write_bytes(marshal.dumps(table.rows, 4))
    finally:
        TestExecutor.tearDownClass()


def prepare(klass):
    import tests.test_executor as mod

    class Tables(dict):
        def __init__(self, suite, schema):
            self.suite = suite
            self.schema = schema

        def __missing__(self, name):
            rows = marshal.loads((CACHE / (self.suite + "-" + name)).read_bytes())
            table = mod.Table(columns=self.schema[name], rows=rows)
            self[name] = table
            return table

    for suite, schema, folder, file in (
        ("tpch", mod.TPCH_SCHEMA, mod.DIR_TPCH, "tpc-h/tpc-h"),
        ("tpcds", mod.TPCDS_SCHEMA, mod.DIR_TPCDS, "tpc-ds/tpc-ds"),
    ):
        conn = mod.duckdb.connect(config={"threads": 1})
        setattr(klass, suite + "_conn", conn)
        setattr(klass, suite + "_tables", Tables(suite, schema))
        setattr(
            klass,
            suite + "_sqls",
            list(mod.load_sql_fixture_pairs("optimizer/" + file + ".sql")),
        )
        for name, columns in schema.items():
            conn.execute(
                f"CREATE VIEW {name} AS SELECT * FROM READ_CSV('{folder}{name}.csv.gz', delim='|', header=True, columns={columns})"
            )
    klass.cache = {}


if __name__ == "__main__":
    import sys

    build(Path(sys.argv[1]).resolve())
