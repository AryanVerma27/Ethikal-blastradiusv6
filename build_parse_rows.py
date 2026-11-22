"""Attribute clean parser calls to the fixture rows exercising them."""

import concurrent.futures
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from boundary import describe
from build_map import InlinePool
from cases import OPT_FILES, TABLES, fixture_cases, run_cases


def build(source, output):
    source, output = source.resolve(), output.resolve()
    sys.path.insert(0, str(source))
    os.chdir(source)
    concurrent.futures.ProcessPoolExecutor = InlinePool
    from sqlglot.dialects import Dialect

    def wrapped(fn):
        def call(dialect, *args, **kwargs):
            nonlocal depth
            if depth:
                return fn(dialect, *args, **kwargs)
            depth += 1
            try:
                result = fn(dialect, *args, **kwargs)
                try:
                    spec = describe(dialect, fn.__name__, args, kwargs)
                    encoded = json.dumps(spec, separators=(",", ":")).encode()
                    rows.setdefault(hashlib.sha256(encoded).hexdigest(), set()).add(
                        index
                    )
                except (TypeError, ValueError):
                    pass
                return result
            finally:
                depth -= 1

        return call

    mapping = {}
    depth = 0
    with patch.multiple(
        Dialect, **{k: wrapped(getattr(Dialect, k)) for k in ("parse", "parse_into")}
    ):
        for name in [*TABLES, *OPT_FILES]:
            path, cls, _ = name.split("::")
            klass = getattr(importlib.import_module(path[:-3].replace("/", ".")), cls)
            rows = {}
            index = -1
            klass.setUpClass()
            valid = True
            try:
                fixtures = fixture_cases(klass, name)
                for index in range(len(fixtures)):
                    if not run_cases(klass, name, fixtures, [index]):
                        valid = False
                        break
            finally:
                index = -1
                klass.tearDownClass()
                klass.doClassCleanups()
            if valid:
                mapping[name] = {k: sorted(ids) for k, ids in rows.items()}
    output.write_text(json.dumps(mapping, separators=(",", ":")))


if __name__ == "__main__":
    build(*map(Path, sys.argv[1:]))
