"""Trace output-producing SQL calls inside the original TPC test assertions."""

import concurrent.futures
import logging
import marshal
import os
import sys
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED
from build_map import InlinePool
from boundary import pack
from cases import TABLES, OPT_FILES, fixture_cases, run_cases
from sql_outputs import encode, digest
from stages import encode_expr


def main(source, output):
    source, output = source.resolve(), output.resolve()
    os.chdir(source)
    sys.path.insert(0, str(source))
    concurrent.futures.ProcessPoolExecutor = InlinePool
    logging.getLogger().addHandler(logging.NullHandler())
    from sqlglot.dialects import Dialect
    import sqlglot
    from tests.test_executor import TestExecutor
    from tests.test_optimizer import TestOptimizer

    orig = Dialect.generate
    prefix = str(source) + "/"
    monitor = sys.monitoring
    hits = {}
    paths = {}

    def line(code, number):
        file = code.co_filename
        if file not in paths:
            path = file[len(prefix) :] if file.startswith(prefix) else ""
            paths[file] = (
                path
                if path in ("sqlglot/generator.py", "sqlglot/transforms.py")
                or path.startswith("sqlglot/generators/")
                else None
            )
        if paths[file]:
            hits.setdefault(paths[file], set()).add(number)
        return monitor.DISABLE

    monitor.use_tool_id(3, "SQL output coverage")
    monitor.register_callback(3, monitor.events.LINE, line)
    monitor.set_events(3, monitor.events.LINE)

    def take():
        out = hits.copy()
        hits.clear()
        monitor.restart_events()
        return out

    def merge(dest, added):
        for path, lines in added.items():
            dest.setdefault(path, set()).update(lines)

    saved = set()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=2) as archive:
        for test in [*TABLES, *OPT_FILES]:
            klass = TestExecutor if test in TABLES else TestOptimizer
            take()
            klass.setUpClass()
            setup = take()
            cases = fixture_cases(klass, test)
            data = {}
            for i in range(len(cases)):
                outside = {key: val.copy() for key, val in setup.items()}
                calls = []
                depth = 0

                def generate(self, expression, copy=True, **opts):
                    nonlocal depth
                    if depth or (not copy and expression.parent is not None):
                        return orig(self, expression, copy=copy, **opts)
                    frame = sys._getframe(1)
                    direct = (
                        frame.f_code is sqlglot.transpile.__code__
                        and frame.f_back
                        and frame.f_back.f_code
                        in (
                            TestExecutor.cached_execute.__code__,
                            TestExecutor.test_optimized_tpch.__code__,
                        )
                    )
                    merge(outside, take())
                    try:
                        raw = encode(
                            [
                                pack(type(self)),
                                pack(self.settings),
                                list(self.version),
                                self.normalization_strategy.name,
                                pack(opts),
                                copy,
                                encode_expr(expression),
                            ]
                        )
                    except (ValueError, TypeError, RecursionError):
                        return orig(self, expression, copy=copy, **opts)
                    take()
                    depth += 1
                    try:
                        sql = orig(self, expression, copy=copy, **opts)
                    finally:
                        depth -= 1
                    touched = take()
                    if touched:
                        key = digest(raw)
                        if key not in saved:
                            archive.writestr(key, raw)
                            saved.add(key)
                        expected = digest(
                            encode([sql, None if copy else encode_expr(expression)])
                        )
                        calls.append((key, expected, touched, sql if direct else None))
                    return sql

                take()
                with patch.object(Dialect, "generate", generate):
                    valid = run_cases(klass, test, cases, [i])
                merge(outside, take())
                if valid:
                    data[i] = (outside, calls)
            klass.tearDownClass()
            klass.doClassCleanups()
            archive.writestr("records/" + test, marshal.dumps(data, 4))
            print(test, len(data), flush=True)
    monitor.set_events(3, 0)


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
