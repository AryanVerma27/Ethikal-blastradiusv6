"""Capture rule inputs, outputs and line coverage for clean executor queries."""

import importlib
import importlib.machinery
import concurrent.futures
import os
import unittest
from build_map import InlinePool
from profile_store import Store
import marshal
import pickle
import sys
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from checkpoints import digest, encode
from stages import signature
from cases import TABLES


def main(source, output):
    source, output = source.resolve(), output.resolve()
    os.chdir(source)
    concurrent.futures.ProcessPoolExecutor = InlinePool
    sys.path.insert(0, str(source))
    original = importlib.machinery.SourceFileLoader.get_code
    target = str(source / "sqlglot/optimizer/optimizer.py")

    def load(loader, name):
        if loader.path != target:
            return original(loader, name)
        text = Path(target).read_text()
        marker = "optimized = rule(optimized, **rule_kwargs)"
        assert text.count(marker) == 1
        return compile(
            text.replace(marker, "optimized = _observe(rule, optimized, rule_kwargs)"),
            target,
            "exec",
            dont_inherit=True,
        )

    importlib.machinery.SourceFileLoader.get_code = load
    optimizer = importlib.import_module("sqlglot.optimizer.optimizer")
    from sqlglot import parse_one
    from sqlglot.schema import ensure_schema
    from tests.helpers import TPCH_SCHEMA, TPCDS_SCHEMA, load_sql_fixture_pairs

    prefix = str(source) + "/"
    monitoring = sys.monitoring
    paths = {}
    hits = {}

    def line(code, number):
        file = code.co_filename
        if file not in paths:
            paths[file] = (
                file[len(prefix) :]
                if file.startswith(prefix + "sqlglot/optimizer/")
                else None
            )
        if paths[file]:
            hits.setdefault(paths[file], set()).add(number)
        return monitoring.DISABLE

    monitoring.use_tool_id(3, "optimizer checkpoint coverage")
    monitoring.register_callback(3, monitoring.events.LINE, line)
    monitoring.set_events(3, monitoring.events.LINE)

    def take():
        chunk = hits.copy()
        hits.clear()
        monitoring.restart_events()
        return chunk

    saved = set()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=2) as archive:

        def store(raw):
            key = digest(raw)
            if key not in saved:
                archive.writestr(key, raw)
                saved.add(key)
            return key

        def observe(rule, expr, kwargs):
            for path, lines in take().items():
                outside.setdefault(path, set()).update(lines)
            before = store(encode(expr))
            arguments = store(pickle.dumps(kwargs, protocol=4))
            take()
            value = rule(expr, **kwargs)
            touched = take()
            steps.append(
                (
                    touched,
                    (
                        rule.__module__,
                        rule.__name__,
                        before,
                        arguments,
                        digest(encode(value)),
                        digest(pickle.dumps(kwargs, protocol=4)),
                    ),
                )
            )
            take()
            return value

        optimizer._observe = observe
        for name, (_, file) in TABLES.items():
            if "test_execute_" not in name:
                continue
            schema = TPCH_SCHEMA if name.endswith("_tpch") else TPCDS_SCHEMA
            for i, (meta, sql, _) in enumerate(
                load_sql_fixture_pairs(file.removeprefix("tests/fixtures/"))
            ):
                if not meta.get("execute"):
                    continue
                expr = pickle.loads(pickle.dumps(parse_one(sql)))
                outside = {}
                steps = []
                take()
                optimized = optimizer.optimize(
                    expr,
                    ensure_schema(schema, dialect=None),
                    leave_tables_isolated=True,
                    dialect=None,
                )
                for path, lines in take().items():
                    outside.setdefault(path, set()).update(lines)
                archive.writestr(
                    f"records/{name}/{i}",
                    marshal.dumps(
                        (outside, steps, signature("optimize", optimized)), 4
                    ),
                )
        from tests.test_optimizer import TestOptimizer

        meta = Store().meta
        # Target the table-driven optimizer methods already identified by the
        # pristine fixture profiler. Include setup and assertions in coverage.
        for name, groups in meta["groups"].items():
            if "rows" not in groups or not name.startswith("tests/test_optimizer.py::"):
                continue
            rows = groups["rows"][2]
            if len(rows) < 15:
                continue
            outside = {}
            steps = []
            take()
            TestOptimizer.setUpClass()
            try:
                result = unittest.TestResult()
                TestOptimizer(name.split("::")[-1]).run(result)
            finally:
                TestOptimizer.tearDownClass()
                TestOptimizer.doClassCleanups()
            for path, lines in take().items():
                outside.setdefault(path, set()).update(lines)
            if result.wasSuccessful() and not result.skipped and steps:
                archive.writestr(
                    f"records/{name}/-1", marshal.dumps((outside, steps, None), 4)
                )

    monitoring.set_events(3, 0)


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
