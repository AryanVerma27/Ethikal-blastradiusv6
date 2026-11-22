"""Replay optimizer rules from clean, content-addressed inputs."""

import importlib
import json
import pickle
import zlib
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile
from stages import decode_expr, signature


@lru_cache(maxsize=1)
def archive():
    return ZipFile(Path(__file__).with_name("rules.zip"))


@lru_cache(maxsize=1)
def catalog():
    return json.loads(archive().read("index"))


@lru_cache(maxsize=2048)
def same_output(module, name, before, arguments, after):
    fn = getattr(importlib.import_module(module), name)
    expr = decode_expr(json.loads(archive().read(before)))
    kwargs = pickle.loads(archive().read(arguments))
    value = fn(expr, **kwargs)
    if signature("optimize", value) != after:
        return False
    for parent in value.walk():
        for key, argument in parent.args.items():
            items = (
                enumerate(argument)
                if isinstance(argument, list)
                else ((None, argument),)
            )
            for index, child in items:
                if hasattr(child, "arg_key") and (
                    child.parent is not parent
                    or child.arg_key != key
                    or child.index != index
                ):
                    return False
    return True


@lru_cache(maxsize=1)
def query_index():
    return json.loads(Path(__file__).with_name("query-index.json").read_bytes())


def verify_rules(name, entries, source, deadline):
    import time

    for entry in entries:
        if time.process_time() >= deadline:
            return None
        if same_output(*entry):
            continue
        cases = query_index().get(name, {}).get(entry[2], [])
        if cases:
            from tests.test_executor import TestExecutor
            from cases import fixture_cases, run_cases
            import table_fixtures

            if not table_fixtures.CACHE.is_dir():
                return None
            table_fixtures.prepare(TestExecutor)
            try:
                rows = fixture_cases(TestExecutor, name)
                if not run_cases(TestExecutor, name, rows, [cases[0]]):
                    return False
            finally:
                TestExecutor.tearDownClass()
        return None
    return True


@lru_cache(maxsize=1)
def line_profiles():
    return json.loads(
        zlib.decompress(Path(__file__).with_name("rule-lines.z").read_bytes())
    )


def affected_entries(record, changes):
    selected = set()
    profiles = line_profiles()
    for module, locations in changes.items():
        for item in record[module]:
            touched = profiles.get("|".join(item), {}).get(module)
            if (
                touched is None
                or locations is None
                or not locations.isdisjoint(touched)
            ):
                selected.add(tuple(item))
    return sorted(selected)
