import concurrent.futures as cf
import importlib
import json
import logging
import os
import sys
import unittest
import unittest.mock
import zlib
from pathlib import Path
from collections import defaultdict
from build_map import InlinePool
from boundary import describe, digest
from instrument import install


def main(source, catalog, output):
    source, output = source.resolve(), output.resolve()
    tests = json.loads(catalog.read_bytes())["tests"]
    os.chdir(source)
    sys.path.insert(0, str(source))
    cf.ProcessPoolExecutor = InlinePool
    logging.getLogger().addHandler(logging.NullHandler())
    hits = set()
    install(source, hits)
    from sqlglot.dialects import Dialect

    prefix = str(source) + "/"
    mon = sys.monitoring

    def line(code, n):
        if code.co_filename.startswith(prefix) and code.co_flags & 1:
            hits.add((code.co_filename[len(prefix) :], n))
        return mon.DISABLE

    mon.use_tool_id(3, "parse boundary")
    mon.register_callback(3, mon.events.LINE, line)
    mon.set_events(3, mon.events.LINE)
    profiles = {}
    for t in tests:
        if t["seconds"] < 0.3 or t["file"] not in (
            "tests/test_optimizer.py",
            "tests/test_executor.py",
        ):
            continue
        name = t["id"]
        positions = t["file"] != "tests/test_executor.py"
        path, cls, method = name.split("::")
        klass = getattr(importlib.import_module(path[:-3].replace("/", ".")), cls)
        coverage = defaultdict(dict)
        outside = set()
        records = []
        indices = {}
        depth = [0]

        def wrap(fn):
            def call(self, *args, **kwargs):
                if depth[0]:
                    return fn(self, *args, **kwargs)
                before = hits.copy()
                hits.clear()
                mon.restart_events()
                depth[0] += 1
                try:
                    result = fn(self, *args, **kwargs)
                    clean = hits.copy()
                    try:
                        spec = describe(self, fn.__name__, args, kwargs)
                        key = json.dumps(spec, sort_keys=True)
                        if key not in indices:
                            indices[key] = len(records)
                            records.append([spec, digest(result, positions)])
                        ix = indices[key]
                        for file, lno in clean:
                            rows = coverage[file]
                            rows[lno] = rows.get(lno, 0) | (1 << ix)
                    except (TypeError, ValueError):
                        outside.update(clean)
                    return result
                except BaseException:
                    outside.update(hits)
                    raise
                finally:
                    depth[0] -= 1
                    hits.clear()
                    hits.update(before)
                    mon.restart_events()

            return call

        hits.clear()
        mon.restart_events()
        with unittest.mock.patch.multiple(
            Dialect, **{k: wrap(getattr(Dialect, k)) for k in ("parse", "parse_into")}
        ):
            klass.setUpClass()
            result = unittest.TestResult()
            klass(method).run(result)
            klass.tearDownClass()
            klass.doClassCleanups()
        if not records or not result.wasSuccessful() or result.skipped:
            continue
        outside.update(hits)
        for file, lno in outside:
            rows = coverage[file]
            rows[lno] = rows.get(lno, 0) | (1 << len(records))
        masks = {}

        def intern(b):
            if b not in masks:
                masks[b] = len(masks)
            return masks[b]

        lines = {
            p: {l: intern(b) for l, b in entries.items()}
            for p, entries in coverage.items()
        }
        profiles[name] = {
            "rows": records,
            "lines": lines,
            "masks": [format(b, "x") for b in masks],
            "positions": positions,
        }
        print(method, len(records), flush=True)
    mon.set_events(3, 0)
    output.write_bytes(
        zlib.compress(json.dumps(profiles, separators=(",", ":")).encode(), 9)
    )


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
