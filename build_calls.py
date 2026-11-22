"""Trace standalone SQL validation calls in upstream dialect tests."""

import ast
import importlib
import json
import logging
import os
import sys
import unittest
import zlib
from collections import defaultdict
from pathlib import Path
from instrument import install
from calls import key_for, METHODS


def main(source, catalog, output):
    source, output = source.resolve(), output.resolve()
    tests = json.loads(catalog.read_bytes())["tests"]
    os.chdir(source)
    sys.path.insert(0, str(source))
    logging.getLogger().addHandler(logging.NullHandler())
    hits = set()
    install(source, hits)
    from tests.dialects.test_dialect import Validator

    monitor = sys.monitoring
    prefix = str(source) + "/"

    def record(code, line):
        if code.co_filename.startswith(prefix) and code.co_flags & 1:
            hits.add((code.co_filename[len(prefix) :], line))
        return monitor.DISABLE

    monitor.use_tool_id(3, "SQL assertions")
    monitor.register_callback(3, monitor.events.LINE, record)
    monitor.set_events(3, monitor.events.LINE)
    cache = {}
    profiles = {}
    for t in tests:
        file = t["file"]
        if not file.startswith("tests/dialects/") or t["seconds"] < 0.015:
            continue
        if file not in cache:
            tree = ast.parse(Path(file).read_text())
            standalone = {
                n.lineno
                for n in ast.walk(tree)
                if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr in METHODS
            }
            cache[file] = standalone
        cls, method = t["id"].split("::")[1:]
        klass = getattr(importlib.import_module(file[:-3].replace("/", ".")), cls)
        if not issubclass(klass, Validator):
            continue
        coverage = defaultdict(dict)
        outside = set()
        keys = []

        def wrap(fn):
            def call(self, *args, **kwargs):
                caller = sys._getframe(1)
                if (
                    caller.f_code.co_filename != str(source / file)
                    or caller.f_lineno not in cache[file]
                ):
                    return fn(self, *args, **kwargs)
                key = key_for(fn.__name__, caller.f_lineno, args, kwargs)
                if key is None:
                    return fn(self, *args, **kwargs)
                outside.update(hits)
                hits.clear()
                monitor.restart_events()
                try:
                    return fn(self, *args, **kwargs)
                finally:
                    bit = 1 << len(keys)
                    keys.append(key)
                    for p, line in hits:
                        entry = coverage[p]
                        entry[line] = entry.get(line, 0) | bit
                    hits.clear()
                    monitor.restart_events()

            return call

        with unittest.mock.patch.multiple(
            Validator, **{k: wrap(getattr(Validator, k)) for k in METHODS}
        ):
            klass.setUpClass()
            hits.clear()
            monitor.restart_events()
            result = unittest.TestResult()
            klass(method).run(result)
            outside.update(hits)
            klass.tearDownClass()
            klass.doClassCleanups()
        if len(keys) < 2 or not result.wasSuccessful() or result.skipped:
            continue
        for p, line in outside:
            entry = coverage[p]
            entry[line] = entry.get(line, 0) | (1 << len(keys))
        masks = {}

        def intern(b):
            if b not in masks:
                masks[b] = len(masks)
            return masks[b]

        lines = {
            p: {l: intern(b) for l, b in entries.items()}
            for p, entries in coverage.items()
        }
        profiles[t["id"]] = {
            "rows": keys,
            "lines": lines,
            "masks": [format(b, "x") for b in masks],
        }
    monitor.set_events(3, 0)
    output.write_bytes(
        zlib.compress(json.dumps(profiles, separators=(",", ":")).encode(), 9)
    )
    print(len(profiles), sum(len(v["rows"]) for v in profiles.values()))


if __name__ == "__main__":
    import unittest.mock

    main(*map(Path, sys.argv[1:]))
