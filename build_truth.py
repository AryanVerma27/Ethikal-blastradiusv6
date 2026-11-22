"""Record truth combinations of side-effect-free Boolean operands in the clean suite."""

import ast
import builtins
import concurrent.futures
from collections import defaultdict
import importlib
import importlib.machinery
import json
import logging
import marshal
import os
from pathlib import Path
import sys
import unittest
import types
from zipfile import ZipFile, ZIP_DEFLATED
from build_map import InlinePool
import guards
import truth_sites


def main(source, catalog, output):
    source, output = source.resolve(), output.resolve()
    tests = json.loads(catalog.read_bytes())["tests"]
    sys.path.insert(0, str(source))
    os.chdir(source)
    concurrent.futures.ProcessPoolExecutor = InlinePool
    logging.getLogger().addHandler(logging.NullHandler())
    sites = []
    layouts = []
    observed = {}
    unknown = set()
    missing = object()
    simple = (
        list,
        tuple,
        dict,
        set,
        frozenset,
        str,
        bool,
        int,
        float,
        bytes,
        type(None),
    )
    default_truth = {}

    def truth(v):
        typ = type(v)
        if typ in simple:
            return bool(v)
        if typ not in default_truth:
            default_truth[typ] = all(
                "__bool__" not in b.__dict__ and "__len__" not in b.__dict__
                for b in typ.__mro__
            )
        if default_truth[typ]:
            return True
        raise ValueError

    def record(i, local, glob):
        if i in unknown:
            return
        pattern = 0
        try:
            for j, (op, x, y) in enumerate(layouts[i]):
                value = local.get(x, missing)
                if value is missing:
                    raise ValueError
                if op == "truth":
                    bit = truth(value)
                elif op in ("is", "isnot"):
                    other = local.get(y[1], missing) if y[0] == "name" else y[1]
                    if other is missing:
                        raise ValueError
                    bit = (value is other) == (op == "is")
                else:
                    if (
                        glob.get("isinstance", builtins.isinstance)
                        is not builtins.isinstance
                    ):
                        raise ValueError
                    matches = False
                    for mod, key in y:
                        if mod:
                            module = glob.get(mod)
                            if type(module) is not types.ModuleType:
                                raise ValueError
                            cls = vars(module).get(key)
                        else:
                            cls = glob.get(key, getattr(builtins, key, None))
                        if type(cls) is not type:
                            raise ValueError
                        matches |= isinstance(value, cls)
                    bit = matches
                pattern |= int(bit) << j
        except (ValueError, TypeError):
            unknown.add(i)
            return
        observed[i] = observed.get(i, 0) | (1 << pattern)

    builtins._br_profile_truth = record

    def layout(n):
        if isinstance(n, ast.Name):
            return "truth", n.id, None
        if isinstance(n, ast.Compare):
            y = n.comparators[0]
            return (
                "is" if isinstance(n.ops[0], ast.Is) else "isnot",
                n.left.id,
                ("name", y.id) if isinstance(y, ast.Name) else ("const", y.value),
            )
        items = n.args[1].elts if isinstance(n.args[1], ast.Tuple) else [n.args[1]]
        return (
            "instance",
            n.args[0].id,
            [
                (x.value.id, x.attr) if isinstance(x, ast.Attribute) else (None, x.id)
                for x in items
            ],
        )

    class Rewrite(ast.NodeTransformer):
        def guard(self, n):
            self.generic_visit(n)
            if truth_sites.supported(n.test):
                i = len(sites)
                sites.append((self.path, guards.site(self.path, n.test)))
                layouts.append([layout(a) for a in guards.leaves(n.test)])
                loc = ast.Call(ast.Name("locals", ast.Load()), [], [])
                glob = ast.Call(ast.Name("globals", ast.Load()), [], [])
                observe = ast.Call(
                    ast.Name("_br_profile_truth", ast.Load()),
                    [ast.Constant(i), loc, glob],
                    [],
                )
                n.test = ast.copy_location(
                    ast.Subscript(
                        ast.Tuple([observe, n.test], ast.Load()),
                        ast.Constant(1),
                        ast.Load(),
                    ),
                    n.test,
                )
            return n

        visit_If = visit_IfExp = visit_While = guard

    original = importlib.machinery.SourceFileLoader.get_code

    def code(loader, name):
        if not loader.path.startswith(str(source / "sqlglot") + "/"):
            return original(loader, name)
        tree = ast.parse(loader.get_data(loader.path), loader.path)
        r = Rewrite()
        r.path = os.path.relpath(loader.path, source)
        return compile(
            ast.fix_missing_locations(r.visit(tree)),
            loader.path,
            "exec",
            dont_inherit=True,
        )

    importlib.machinery.SourceFileLoader.get_code = code
    from profile_store import Store

    bits = {n: 1 << i for i, n in enumerate(Store().meta["names"])}
    selections = defaultdict(dict)
    groups = defaultdict(list)
    for test in tests:
        file, cls, method = test["id"].split("::")
        groups[file, cls].append((test["id"], method))
    for (file, cls), methods in groups.items():
        klass = getattr(importlib.import_module(file[:-3].replace("/", ".")), cls)
        observed.clear()
        unknown.clear()
        klass.setUpClass()
        shared = observed.copy()
        uncertain = unknown.copy()
        for name, method in methods:
            observed.clear()
            unknown.clear()
            result = unittest.TestResult()
            klass(method).run(result)
            if not result.wasSuccessful() or result.skipped:
                continue
            for i in (observed.keys() | shared.keys()) - (unknown | uncertain):
                patterns = observed.get(i, 0) | shared.get(i, 0)
                table = selections[i]
                table[patterns] = table.get(patterns, 0) | bits[name]
        klass.tearDownClass()
        klass.doClassCleanups()
        print(file, flush=True)
    byfile = defaultdict(dict)
    for i, table in selections.items():
        path, site = sites[i]
        byfile[path][site] = table
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path, entries in byfile.items():
            archive.writestr(path, marshal.dumps(entries, 4))


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
