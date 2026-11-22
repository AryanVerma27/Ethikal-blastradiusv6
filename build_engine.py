import concurrent.futures as cf
import json
import logging
import os
import sys
import zlib
from collections import defaultdict
from pathlib import Path
from unittest import mock
from build_map import InlinePool
from cases import TABLES, fixture_cases, run_cases
from engine import plan_key
from instrument import install


def main(source, output):
    source, output = source.resolve(), output.resolve()
    os.chdir(source)
    sys.path.insert(0, str(source))
    cf.ProcessPoolExecutor = InlinePool
    logging.getLogger().addHandler(logging.NullHandler())
    hits = set()
    install(source, hits)
    import tests.test_executor as mod
    from sqlglot.executor.python import PythonExecutor

    prefix = str(source) + "/"
    mon = sys.monitoring

    def line(code, n):
        if code.co_filename.startswith(prefix) and code.co_flags & 1:
            hits.add((code.co_filename[len(prefix) :], n))
        return mon.DISABLE

    mon.use_tool_id(3, "row engine")
    mon.register_callback(3, mon.events.LINE, line)
    mon.set_events(3, mon.events.LINE)
    profiles = {}
    for name in TABLES:
        if "test_execute_" not in name:
            continue
        klass = mod.TestExecutor
        hits.clear()
        mon.restart_events()
        klass.setUpClass()
        setup = hits.copy()
        rows = fixture_cases(klass, name)
        coverage = defaultdict(dict)
        records = []
        original = PythonExecutor.execute
        for i, (meta, sql, _) in enumerate(rows):
            inside = set()
            keys = []

            def wrapped(self, plan, *args, **kwargs):
                key = plan_key(plan)
                keys.append(key)
                hits.clear()
                mon.restart_events()
                try:
                    with mock.patch.object(PythonExecutor, "execute", original):
                        return original(self, plan, *args, **kwargs)
                finally:
                    inside.update(hits)

            reference = (
                mod.transpile(sql, write="duckdb")[0] if meta.get("execute") else None
            )
            with mock.patch.object(PythonExecutor, "execute", wrapped):
                assert run_cases(klass, name, rows, [i]), (name, i)
            records.append([reference, keys[0] if len(keys) == 1 else None])
            for p, l in inside:
                ls = coverage[p]
                ls[l] = ls.get(l, 0) | (1 << i)
        for p, l in setup:
            ls = coverage[p]
            ls[l] = ls.get(l, 0) | (1 << len(rows))
        masks = {}

        def intern(bits):
            if bits not in masks:
                masks[bits] = len(masks)
            return masks[bits]

        profiles[name] = {
            "rows": records,
            "lines": {
                p: {l: intern(b) for l, b in ls.items()} for p, ls in coverage.items()
            },
            "masks": [format(b, "x") for b in masks],
        }
        klass.tearDownClass()
        klass.doClassCleanups()
    mon.set_events(3, 0)
    output.write_bytes(
        zlib.compress(json.dumps(profiles, separators=(",", ":")).encode(), 9)
    )


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
