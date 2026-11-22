"""Measure which source lines execute on each side of a TPC transformation."""

import concurrent.futures as cf
import json
import base64
import zlib
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
from build_map import InlinePool, save_map
from cases import TABLES, fixture_cases, run_cases
from instrument import install
from stages import signature, encode_expr


def main(source, output):
    source, output = source.resolve(), output.resolve()
    os.chdir(source)
    sys.path.insert(0, str(source))
    cf.ProcessPoolExecutor = InlinePool
    logging.getLogger().addHandler(logging.NullHandler())
    hits = set()
    observed = install(source, hits)
    prefix = str(source) + "/"

    def record(code, line):
        if code.co_filename.startswith(prefix) and code.co_flags & 1:
            hits.add((code.co_filename[len(prefix) :], line))
        return sys.monitoring.DISABLE

    import tests.test_executor as module
    import sqlglot.executor as executor

    monitor = sys.monitoring
    monitor.use_tool_id(3, "stage coverage")
    monitor.register_callback(3, monitor.events.LINE, record)
    monitor.set_events(3, monitor.events.LINE)
    coverage = defaultdict(dict)
    records = []
    plan_inputs = {}

    def capture(name, index, stage, digest, cost):
        bit = 1 << len(records)
        records.append([name, index, stage, digest, round(cost, 5)])
        for path, line in hits:
            entries = coverage[path]
            entries[line] = entries.get(line, 0) | bit

    for name in TABLES:
        if "test_execute_" not in name:
            continue
        print(name, flush=True)
        klass = module.TestExecutor
        hits.clear()
        monitor.restart_events()
        klass.setUpClass()
        capture(name, -1, "setup", "", 0)
        items = fixture_cases(klass, name)
        for i in range(len(items)):
            outside = set()

            def wrapper(stage, fn):
                def invoke(*args, **kwargs):
                    outside.update(hits)
                    if stage == "plan":
                        raw = json.dumps(
                            encode_expr(args[0]), separators=(",", ":")
                        ).encode()
                        plan_inputs[f"{name}/{i}"] = base64.b64encode(
                            zlib.compress(raw, 9)
                        ).decode()
                    hits.clear()
                    monitor.restart_events()
                    start = time.process_time()
                    value = fn(*args, **kwargs)
                    cost = time.process_time() - start
                    saved = hits.copy()
                    digest = signature(stage, value)
                    hits.clear()
                    hits.update(saved)
                    capture(name, i, stage, digest, cost)
                    hits.clear()
                    monitor.restart_events()
                    return value

                return invoke

            with ExitStack() as stack:
                for obj, attr, stage in [
                    (module, "parse_one", "parse"),
                    (module, "transpile", "reference"),
                    (executor, "optimize", "optimize"),
                    (executor, "Plan", "plan"),
                ]:
                    stack.enter_context(
                        patch.object(obj, attr, wrapper(stage, getattr(obj, attr)))
                    )
                hits.clear()
                monitor.restart_events()
                if not run_cases(klass, name, items, [i]):
                    raise RuntimeError((name, i))
                hits.update(outside)
                capture(name, i, "outside", "", 0)
        klass.tearDownClass()
        klass.doClassCleanups()
    monitor.set_events(3, 0)
    save_map(output, records, coverage, {}, observed)
    data = json.loads(output.read_bytes())
    data["plan_inputs"] = plan_inputs
    output.write_text(json.dumps(data, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
