"""Collect baseline line and fixture coverage with a single-process unittest runner."""

import concurrent.futures as cf
import importlib
import json
import os
import pickle
import sys
import unittest
from pathlib import Path
from collections import defaultdict


class InlinePool:
    def __init__(self, *args, initializer=None, initargs=(), **kwargs):
        if initializer:
            initializer(*initargs)

    def submit(self, fn, *args, **kwargs):
        future = cf.Future()
        try:
            args, kwargs = pickle.loads(pickle.dumps((args, kwargs)))
            value = fn(*args, **kwargs)
            future.set_result(pickle.loads(pickle.dumps(value)))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def save_map(destination, names, coverage, fixtures, observed):
    # Many nearby lines have identical coverage. Store each bitset once.
    indices = {}

    def index(mask):
        if mask not in indices:
            indices[mask] = len(indices)
        return indices[mask]

    lines = {
        path: {number: index(bits) for number, bits in sorted(mapping.items())}
        for path, mapping in sorted(coverage.items())
    }
    files = {path: index(bits) for path, bits in sorted(fixtures.items())}
    data = dict(
        tests=names,
        masks=[format(mask, "x") for mask in indices],
        lines=lines,
        fixtures=files,
        types={k: sorted(v) for k, v in sorted(observed.items())},
    )
    destination.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def main():
    source, catalog, destination = map(Path, sys.argv[1:])
    source = source.resolve()
    tests = json.loads(catalog.read_text())["tests"]
    destination = destination.resolve()
    os.chdir(source)
    sys.path.insert(0, str(source))
    cf.ProcessPoolExecutor = InlinePool
    paths = {}
    hits = set()
    files = set()

    def record(code, line):
        name = code.co_filename
        if name not in paths:
            paths[name] = (
                name.removeprefix(str(source) + "/")
                if name.startswith(str(source) + "/")
                else None
            )
        if paths[name] and code.co_flags & 1:
            hits.add((paths[name], line))
        return sys.monitoring.DISABLE

    def audit(event, args):
        if event == "open" and isinstance(args[0], str) and "fixtures/" in args[0]:
            files.add(os.path.relpath(os.path.abspath(args[0]), source))

    sys.addaudithook(audit)
    from instrument import install

    observed = install(source, hits)
    import logging

    logging.getLogger().addHandler(logging.NullHandler())
    monitor = sys.monitoring
    monitor.use_tool_id(3, "coverage")
    monitor.register_callback(3, monitor.events.LINE, record)
    monitor.set_events(3, monitor.events.LINE)
    coverage = defaultdict(dict)
    fixtures = defaultdict(int)
    names = []
    by_class = defaultdict(list)
    for t in tests:
        file, cls, method = t["id"].split("::")
        by_class[file, cls].append((t["id"], method))

    for (file, cls), members in by_class.items():
        print(file, flush=True, file=sys.stderr)
        try:
            klass = getattr(importlib.import_module(file[:-3].replace("/", ".")), cls)
            hits.clear()
            files.clear()
            monitor.restart_events()
            klass.setUpClass()
        except Exception as exc:
            print(file, exc, file=sys.stderr)
            continue
        shared, shared_files = hits.copy(), files.copy()
        for name, method in members:
            hits.clear()
            files.clear()
            monitor.restart_events()
            result = unittest.TestResult()
            klass(method).run(result)
            if result.errors or result.failures:
                print(
                    "baseline failure",
                    name,
                    (result.errors or result.failures)[0][1][-220:],
                    file=sys.stderr,
                )
            bit = 1 << len(names)
            names.append(name)
            for path, line in hits | shared:
                lines = coverage[path]
                lines[line] = lines.get(line, 0) | bit
            for path in files | shared_files:
                fixtures[path] |= bit
        klass.tearDownClass()
        klass.doClassCleanups()
    monitor.set_events(3, 0)
    save_map(destination, names, coverage, fixtures, observed)


if __name__ == "__main__":
    main()
