"""Compare actual SQL generation for queries touched by generator changes."""

import hashlib
import json
import marshal
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile
from boundary import unpack
from stages import decode_expr, encode_expr


def encode(value):
    return json.dumps(value, separators=(",", ":")).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@lru_cache(maxsize=1)
def archive():
    return ZipFile(Path(__file__).with_name("sql-outputs.zip"))


@lru_cache(maxsize=8)
def records(name):
    try:
        return marshal.loads(archive().read("records/" + name))
    except KeyError:
        return {}


@lru_cache(maxsize=1024)
def matches(key, expected, reference, test):
    cls, settings, version, strategy, opts, copy, ast = json.loads(archive().read(key))
    dialect = unpack(cls)(
        version=".".join(map(str, version)),
        normalization_strategy=strategy,
        **unpack(settings),
    )
    expression = decode_expr(ast)
    sql = dialect.generate(expression, copy=copy, **unpack(opts))
    if digest(encode([sql, None if copy else encode_expr(expression)])) == expected:
        return True
    if reference is None:
        return False
    if sql == reference:
        return True
    tpch = test.endswith("_tpch")
    conn = bound_connection(tpch)
    # Only a single read-only query is eligible for comparing result tables.
    import duckdb

    statements = conn.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        return False
    if parsed_query(conn, reference) == parsed_query(conn, sql):
        if conn.sql(reference).columns == conn.sql(sql).columns:
            return True
    conn = connection(tpch)
    old = conn.execute(reference).fetchdf()
    new = conn.execute(sql).fetchdf()
    return old.equals(new) and old.columns.equals(new.columns)


@lru_cache(maxsize=2)
def bound_connection(tpch):
    from table_fixtures import CACHE
    import duckdb

    return duckdb.connect(
        str(CACHE / ("tpch.duckdb" if tpch else "tpcds.duckdb")),
        read_only=True,
        config={"threads": 1},
    )


def parsed_query(conn, sql):
    raw = conn.execute(
        "SELECT json_serialize_sql(?, skip_empty := true, skip_null := true)", [sql]
    ).fetchone()[0]

    def remove_positions(obj):
        if "class" in obj or "type" in obj:
            for key in ("query_location", "query_location_length"):
                if type(obj.get(key)) is int:
                    obj.pop(key)
        return obj

    parsed = json.loads(raw, object_hook=remove_positions)
    if parsed.get("error") or len(parsed.get("statements", ())) != 1:
        raise ValueError("unsupported reference query")
    return parsed


@lru_cache(maxsize=2)
def connection(tpch):
    from tests import test_executor as mod

    schema, folder = (
        (mod.TPCH_SCHEMA, mod.DIR_TPCH) if tpch else (mod.TPCDS_SCHEMA, mod.DIR_TPCDS)
    )
    conn = mod.duckdb.connect(config={"threads": 1})
    for name, columns in schema.items():
        conn.execute(
            f"CREATE VIEW {name} AS SELECT * FROM READ_CSV('{folder}{name}.csv.gz', delim='|', header=True, columns={columns})"
        )
    return conn


def residual(test, indices, locations):
    def touches(rows):
        return any(
            not lines.isdisjoint(rows.get(path, ()))
            for path, lines in locations.items()
        )

    data = records(test)
    unknown = []
    for i in indices:
        if i not in data:
            unknown.append(i)
            continue
        outside, calls = data[i]
        if touches(outside) or any(
            touches(lines) and not matches(key, expected, reference, test)
            for key, expected, lines, reference in calls
        ):
            unknown.append(i)
    return unknown
