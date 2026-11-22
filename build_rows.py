"""Trace SQL fixture iterations in the pinned optimizer tests."""

import concurrent.futures as cf
import json
import logging
import os
import sys
import unittest
import zlib
from collections import defaultdict
from pathlib import Path
from build_map import InlinePool
from instrument import install


def main(source, catalog, output):
    source, output = source.resolve(), output.resolve()
    tests = json.loads(catalog.read_bytes())["tests"]
    os.chdir(source)
    sys.path.insert(0, str(source))
    cf.ProcessPoolExecutor = InlinePool
    hits = set()
    install(source, hits)
    import tests.test_optimizer as mod

    logging.getLogger().addHandler(logging.NullHandler())
    prefix = str(source) + "/"
    monitor = sys.monitoring

    def record(code, line):
        if code.co_filename.startswith(prefix) and code.co_flags & 1:
            hits.add((code.co_filename[len(prefix) :], line))
        return monitor.DISABLE

    monitor.use_tool_id(3, "fixture rows")
    monitor.register_callback(3, monitor.events.LINE, record)
    monitor.set_events(3, monitor.events.LINE)
    k = mod.TestOptimizer
    k.setUpClass()
    loader = mod.load_sql_fixture_pairs
    check = k.check_file
    profiles = {}
    for t in tests:
        if t["file"] != "tests/test_optimizer.py":
            continue
        method = t["id"].split("::")[-1]
        # Discover fixture dependencies by executing the unmodified test.
        files = {}
        coverage = defaultdict(dict)
        paths = []
        outside = set()
        current = [None]

        def collect():
            if current[0] is None:
                outside.update(hits)
            else:
                bit = 1 << current[0]
                for p, line in hits:
                    by_line = coverage[p]
                    by_line[line] = by_line.get(line, 0) | bit
            hits.clear()
            monitor.restart_events()

        def rows(path):
            for j, row in enumerate(loader(path)):
                collect()
                ix = len(paths)
                paths.append((path, j))
                current[0] = ix
                yield row
                collect()
                current[0] = None

        # check_file submits all rows before asserting their results. Execute
        # one unchanged iteration at a time to keep its assertions in scope.
        def split_check(self, file, *args, **kwargs):
            path = f"optimizer/{file}.sql"
            for row in rows(path):
                with unittest.mock.patch.object(
                    mod, "load_sql_fixture_pairs", lambda ignored: [row]
                ):
                    check(self, file, *args, **kwargs)

        hits.clear()
        monitor.restart_events()
        with (
            unittest.mock.patch.object(k, "check_file", split_check),
            unittest.mock.patch.object(mod, "load_sql_fixture_pairs", rows),
        ):
            result = unittest.TestResult()
            k(method).run(result)
        collect()
        if not paths or not result.wasSuccessful() or result.skipped:
            continue
        # An outside hit is represented as the bit above the last row.
        for p, line in outside:
            ls = coverage[p]
            ls[line] = ls.get(line, 0) | (1 << len(paths))
        masks = {}

        def intern(bits):
            if bits not in masks:
                masks[bits] = len(masks)
            return masks[bits]

        lines = {
            p: {l: intern(b) for l, b in entries.items()}
            for p, entries in coverage.items()
        }
        profiles[t["id"]] = {
            "rows": paths,
            "lines": lines,
            "masks": [format(b, "x") for b in masks],
        }
        print(method, len(paths), flush=True)
    monitor.set_events(3, 0)
    k.tearDownClass()
    k.doClassCleanups()
    output.write_bytes(
        zlib.compress(json.dumps(profiles, separators=(",", ":")).encode(), 9)
    )


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
