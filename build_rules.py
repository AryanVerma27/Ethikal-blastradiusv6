"""Record rule inputs and module calls while running clean upstream tests."""

import concurrent.futures
import hashlib
import importlib
import importlib.machinery
import json
import logging
import os
from pathlib import Path
import pickle
import sys
import unittest
from zipfile import ZIP_DEFLATED, ZipFile
from build_map import InlinePool
from stages import encode_expr, signature


def main(source, tests_file, output):
    source, output = source.resolve(), output.resolve()
    tests = json.loads(tests_file.read_bytes())["tests"]
    os.chdir(source)
    sys.path.insert(0, str(source))
    concurrent.futures.ProcessPoolExecutor = InlinePool
    logging.getLogger().addHandler(logging.NullHandler())
    loader = importlib.machinery.SourceFileLoader.get_code
    target = str(source / "sqlglot/optimizer/optimizer.py")

    def load(self, name):
        if self.path == target:
            original = Path(target).read_text()
            call = "optimized = rule(optimized, **rule_kwargs)"
            assert original.count(call) == 1
            return compile(
                original.replace(
                    call, "optimized = _capture(rule, optimized, rule_kwargs)"
                ),
                target,
                "exec",
                dont_inherit=True,
            )
        return loader(self, name)

    importlib.machinery.SourceFileLoader.get_code = load
    optimizer = importlib.import_module("sqlglot.optimizer.optimizer")
    known = {fn.__code__.co_filename: fn.__module__ for fn in optimizer.RULES}
    monitor = sys.monitoring
    reached = set()

    def observe(code, _):
        if code.co_filename in known:
            reached.add(known[code.co_filename])
        return monitor.DISABLE

    monitor.use_tool_id(3, "optimizer rule calls")
    monitor.register_callback(3, monitor.events.PY_START, observe)
    monitor.set_events(3, monitor.events.PY_START)
    index = {}
    blobs = {}
    scope = None

    def save(raw):
        key = hashlib.sha256(raw).hexdigest()
        blobs.setdefault(key, raw)
        return key

    def capture(fn, expr, kwargs):
        nonlocal scope
        if scope is None:
            return fn(expr, **kwargs)
        outer = set(reached)
        reached.clear()
        monitor.restart_events()
        before = json.dumps(encode_expr(expr), separators=(",", ":")).encode()
        arguments = pickle.dumps(kwargs, protocol=4)
        try:
            value = fn(expr, **kwargs)
            record = [
                fn.__module__,
                fn.__name__,
                save(before),
                save(arguments),
                signature("optimize", value),
            ]
            for module in reached:
                scope.setdefault(module, {})[tuple(record)] = record
            return value
        finally:
            reached.clear()
            reached.update(outer)
            monitor.restart_events()

    optimizer._capture = capture
    for test in tests:
        if (
            test["file"] not in ("tests/test_executor.py", "tests/test_optimizer.py")
            or test["seconds"] < 0.5
        ):
            continue
        name = test["id"]
        path, cls, method = name.split("::")
        klass = getattr(importlib.import_module(path[:-3].replace("/", ".")), cls)
        scope = {}
        reached.clear()
        monitor.restart_events()
        klass.setUpClass()
        result = unittest.TestResult()
        klass(method).run(result)
        klass.tearDownClass()
        klass.doClassCleanups()
        if result.wasSuccessful() and not result.skipped:
            index[name] = {
                module: list(records.values())
                for module, records in scope.items()
                if module not in reached
            }
        print(name, len(index.get(name, {})), flush=True)
        scope = None
    used = {
        k
        for modules in index.values()
        for records in modules.values()
        for record in records
        for k in record[2:4]
    }
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=8) as z:
        for key in sorted(used):
            z.writestr(key, blobs[key])
        z.writestr("index", json.dumps(index, separators=(",", ":")))
    monitor.set_events(3, 0)


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
