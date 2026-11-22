"""Collect per-iteration coverage and timing against the unmodified upstream source."""

import concurrent.futures as cf
import importlib
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from build_map import InlinePool, save_map
from cases import GENERATOR, OPT_FILES, TABLES, fixture_cases, run_cases
from instrument import install


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

    monitor = sys.monitoring
    monitor.use_tool_id(3, "case coverage")
    monitor.register_callback(3, monitor.events.LINE, record)
    monitor.set_events(3, monitor.events.LINE)
    coverage = defaultdict(dict)
    names, costs, uncertain = [], [], []
    for name in [*TABLES, *OPT_FILES, GENERATOR]:
        path, cls, method = name.split("::")
        klass = getattr(importlib.import_module(path[:-3].replace("/", ".")), cls)
        klass.setUpClass()
        items = fixture_cases(klass, name)
        print(name, len(items), flush=True)
        for i in range(len(items)):
            hits.clear()
            monitor.restart_events()
            start = time.process_time()
            if not run_cases(klass, name, items, [i]):
                uncertain.append(len(names))
            elapsed = time.process_time() - start
            bit = 1 << len(names)
            names.append([name, i])
            costs.append(round(elapsed, 4))
            for file, line in hits:
                entries = coverage[file]
                entries[line] = entries.get(line, 0) | bit
        klass.tearDownClass()
        klass.doClassCleanups()
    monitor.set_events(3, 0)
    save_map(output, names, coverage, {}, observed)
    data = json.loads(output.read_bytes())
    data["cpu"] = costs
    data["uncertain"] = uncertain
    output.write_text(json.dumps(data, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
