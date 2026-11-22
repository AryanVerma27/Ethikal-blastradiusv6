"""Record guarded expression execution in the original test suite."""

import ast
import builtins
import concurrent.futures as cf
import importlib
import importlib.machinery
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from collections import defaultdict
from build_map import InlinePool, save_map
from branches import guarded, get, put, site


def main(source, catalog, output):
    source, output = source.resolve(), output.resolve()
    tests = json.loads(catalog.read_bytes())["tests"]
    os.chdir(source)
    sys.path.insert(0, str(source))
    logging.getLogger().addHandler(logging.NullHandler())
    cf.ProcessPoolExecutor = InlinePool
    builtins._br_branch_marker = None
    locations = []

    class Rewrite(ast.NodeTransformer):
        def generic_visit(self, node):
            super().generic_visit(node)
            for field, i in guarded(node):
                child = get(node, field, i)
                marker = ast.Name("_br_branch_marker", ast.Load())
                locations.append(site(self.path, node, field, i))
                marker.lineno = marker.end_lineno = 1000000 + len(locations) - 1
                marker.col_offset = 0
                marker.end_col_offset = 1
                expr = ast.Subscript(
                    ast.Tuple([marker, child], ast.Load()), ast.Constant(1), ast.Load()
                )
                put(node, field, i, ast.copy_location(expr, child))
            return node

    original = importlib.machinery.SourceFileLoader.get_code

    def get_code(loader, fullname):
        if not loader.path.startswith(str(source / "sqlglot") + "/"):
            return original(loader, fullname)
        rewrite = Rewrite()
        rewrite.path = os.path.relpath(loader.path, source)
        tree = rewrite.visit(ast.parse(loader.get_data(loader.path), loader.path))
        return compile(
            ast.fix_missing_locations(tree), loader.path, "exec", dont_inherit=True
        )

    importlib.machinery.SourceFileLoader.get_code = get_code
    hits = set()

    def line(code, number):
        if number >= 1000000:
            hits.add(number - 1000000)
        return sys.monitoring.DISABLE

    m = sys.monitoring
    m.use_tool_id(3, "branch coverage")
    m.register_callback(3, m.events.LINE, line)
    m.set_events(3, m.events.LINE)
    groups = defaultdict(list)
    for t in tests:
        path, cls, method = t["id"].split("::")
        groups[path, cls].append((t["id"], method))
    masks = defaultdict(dict)
    names = []
    for (path, cls), members in groups.items():
        print(path, flush=True)
        klass = getattr(importlib.import_module(path[:-3].replace("/", ".")), cls)
        hits.clear()
        m.restart_events()
        klass.setUpClass()
        shared = hits.copy()
        for name, method in members:
            hits.clear()
            m.restart_events()
            result = unittest.TestResult()
            klass(method).run(result)
            bit = 1 << len(names)
            names.append(name)
            if not result.wasSuccessful() or result.skipped:
                masks["?"][0] = masks["?"].get(0, 0) | bit
            for index in hits | shared:
                row = masks[locations[index]]
                row[0] = row.get(0, 0) | bit
        klass.tearDownClass()
        klass.doClassCleanups()
    m.set_events(3, 0)
    for loc in locations:
        masks.setdefault(loc, {})
    save_map(output, names, masks, {}, {})


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
