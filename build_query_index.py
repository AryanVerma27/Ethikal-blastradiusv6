"""Relate clean optimizer rule inputs to actual executor fixture iterations."""

import hashlib
import json
import pickle
import sys
from pathlib import Path
import importlib
import importlib.machinery
from stages import encode_expr
from cases import TABLES


def main(source, output):
    source = source.resolve()
    sys.path.insert(0, str(source))
    path = str(source / "sqlglot/optimizer/optimizer.py")
    original = importlib.machinery.SourceFileLoader.get_code

    def load(loader, name):
        if loader.path == path:
            text = Path(path).read_text()
            target = "optimized = rule(optimized, **rule_kwargs)"
            assert text.count(target) == 1
            return compile(
                text.replace(
                    target, "optimized = _capture(rule, optimized, rule_kwargs)"
                ),
                path,
                "exec",
                dont_inherit=True,
            )
        return original(loader, name)

    importlib.machinery.SourceFileLoader.get_code = load
    engine = importlib.import_module("sqlglot.optimizer.optimizer")
    from sqlglot import parse_one
    from sqlglot.schema import ensure_schema
    from tests.helpers import TPCH_SCHEMA, TPCDS_SCHEMA, load_sql_fixture_pairs

    def capture(rule, expr, kwargs):
        before = hashlib.sha256(
            json.dumps(encode_expr(expr), separators=(",", ":")).encode()
        ).hexdigest()
        rows.setdefault(before, set()).add(i)
        return rule(expr, **kwargs)

    engine._capture = capture
    mapping = {}
    for name, (_, fixture) in TABLES.items():
        if "test_execute_" not in name:
            continue
        rows = {}
        for i, (meta, sql, _) in enumerate(
            load_sql_fixture_pairs(fixture.removeprefix("tests/fixtures/"))
        ):
            if meta.get("execute"):
                expression = pickle.loads(pickle.dumps(parse_one(sql)))
                engine.optimize(
                    expression,
                    ensure_schema(
                        TPCH_SCHEMA if name.endswith("_tpch") else TPCDS_SCHEMA
                    ),
                    leave_tables_isolated=True,
                    dialect=None,
                )
        mapping[name] = {k: sorted(v) for k, v in rows.items()}
    output.write_text(json.dumps(mapping, separators=(",", ":")))


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
