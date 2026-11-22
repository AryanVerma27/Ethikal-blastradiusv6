"""Isolate independent iterations of the pinned suite's table-driven tests."""

import runpy
from functools import lru_cache
import unittest
from unittest import mock

EXECUTOR = "tests/test_executor.py::TestExecutor::"
GENERATOR = "tests/test_generator.py::TestGenerator::test_generator_memory"
OPTIMIZER = "tests/test_optimizer.py::TestOptimizer::"
TPCH = "tests/fixtures/optimizer/tpc-h/tpc-h.sql"
TPCDS = "tests/fixtures/optimizer/tpc-ds/tpc-ds.sql"
# The attributes below are assigned by TestExecutor.setUpClass. Each case
# uses the original method, its table data, SQL text and assertions.
TABLES = {
    EXECUTOR + "test_execute_tpcds": ("tpcds_sqls", TPCDS),
    EXECUTOR + "test_execute_tpch": ("tpch_sqls", TPCH),
    EXECUTOR + "test_optimized_tpch": ("tpch_sqls", TPCH),
}
OPT_FILES = {OPTIMIZER + "test_tpcds": TPCDS, OPTIMIZER + "test_tpch": TPCH}


def fixture_cases(klass, name):
    if name == GENERATOR:
        import tests.test_generator as module

        return list(module.Dialects)
    if name in OPT_FILES:
        import tests.test_optimizer as module

        return list(
            module.load_sql_fixture_pairs(
                OPT_FILES[name].removeprefix("tests/fixtures/")
            )
        )
    return getattr(klass, TABLES[name][0])


def run_cases(klass, name, items, indices):
    if name == GENERATOR:
        import tests.test_generator as module

        override = mock.patch.object(module, "Dialects", [items[i] for i in indices])
    elif name in OPT_FILES:
        import tests.test_optimizer as module

        original = module.load_sql_fixture_pairs

        def subset(path):
            if "tests/fixtures/" + path == OPT_FILES[name]:
                return iter(items[i] for i in indices)
            return original(path)

        override = mock.patch.object(module, "load_sql_fixture_pairs", subset)
    else:
        override = mock.patch.object(
            klass, TABLES[name][0], [items[i] for i in indices]
        )
    with override:
        result = unittest.TestResult()
        result.failfast = True
        klass(name.split("::")[-1]).run(result)
        return result.wasSuccessful() and not result.skipped


@lru_cache(maxsize=1)
def pair_helpers(source):
    # Loading by path keeps the unpatched tests package out of the parent.
    helpers = runpy.run_path(str(source / "tests/helpers.py"))
    return helpers["_filter_comments"], helpers["_extract_meta"]


def pair_versions(source, filename, updated):
    """Use the pinned helper's parsing rules, including comment and metadata handling."""
    _filter_comments, _extract_meta = pair_helpers(source)

    def decode(text):
        statements = _filter_comments(text).split(";")
        result = []
        for i in range(0, len(statements) - 1, 2):
            sql, meta = _extract_meta(statements[i].strip())
            result.append((meta, sql, statements[i + 1].strip()))
        return result

    old = decode((source / filename).read_text())
    new = decode(updated)
    if len(old) != len(new):
        return None
    return old, new


def changed_pairs(source, filename, updated):
    versions = pair_versions(source, filename, updated)
    if versions is None:
        return None
    old, new = versions
    affected = {}
    for name, (_, path) in TABLES.items():
        if path != filename:
            continue
        if name.endswith("test_optimized_tpch"):
            affected[name] = [
                i for i, (a, b) in enumerate(zip(old, new)) if a[1:] != b[1:]
            ]
        else:
            affected[name] = [
                i for i, (a, b) in enumerate(zip(old, new)) if a[:2] != b[:2]
            ]
    for name, path in OPT_FILES.items():
        if filename == path:
            affected[name] = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    return affected


def fixture_status(name, indices, source, replacements):
    """Resolve TPC fixture edits when the assertions' inputs are unchanged."""
    from boundary import digest
    from pathlib import Path
    from sqlglot import parse_one, transpile

    if len(replacements) != 1 or not isinstance(indices, list):
        return None
    absolute, text = next(iter(replacements.items()))
    filename = Path(absolute).relative_to(source).as_posix()
    if filename not in (TPCH, TPCDS):
        return None
    versions = pair_versions(source, filename, text)
    if versions is None:
        return None
    old, new = versions
    for i in indices:
        meta, sql, expected = old[i]
        other, edited, target = new[i]
        if name in OPT_FILES and meta == other and sql == edited and expected != target:
            return False
    for i in indices:
        meta, sql, expected = old[i]
        other, edited, target = new[i]
        if meta != other:
            return None
        if name.endswith("test_optimized_tpch"):
            if any(
                a != b and transpile(a, write="duckdb") != transpile(b, write="duckdb")
                for a, b in ((sql, edited), (expected, target))
            ):
                return None
        else:
            if name in OPT_FILES and expected != target:
                return None
            dialect = meta.get("dialect") if name in OPT_FILES else None
            if sql != edited and digest(parse_one(sql, read=dialect), False) != digest(
                parse_one(edited, read=dialect), False
            ):
                return None
    return True
