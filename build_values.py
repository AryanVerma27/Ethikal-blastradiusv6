"""Collect bounded scalar domains at comparisons against literals in the pristine suite."""

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
from collections import defaultdict
from pathlib import Path
from build_map import InlinePool, save_map
import guards


def literal(node):
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError):
        return False
    return type(value) in (int, float, str, bool, tuple, type(None))


def key(path, node):
    return f"{path}:{node.lineno}:{node.col_offset}"


def main(source, catalog, output, boolean=False):
    source, output = source.resolve(), output.resolve()
    tests = json.loads(catalog.read_bytes())["tests"]
    os.chdir(source)
    sys.path.insert(0, str(source))
    cf.ProcessPoolExecutor = InlinePool
    logging.getLogger().addHandler(logging.NullHandler())
    sites, seen, saturated = [], {}, set()

    def record(value, index):
        if index in saturated:
            return value
        typ = type(value)
        if typ is tuple and len(value) <= 4 and all(type(v) is int for v in value):
            encoded = ("tuple", value)
        elif typ in (bool, int, str, type(None)) and (
            typ is not str or len(value) <= 48
        ):
            encoded = (typ.__name__, value)
        else:
            saturated.add(index)
            return value
        vals = seen.setdefault(index, set())
        vals.add(encoded)
        if len(vals) > 12:
            saturated.add(index)
        return value

    builtins._br_value = record
    descriptions = {}
    memo = {}

    def guard(index, env):
        if index in saturated:
            return
        names, atoms = descriptions[index]
        values = tuple(env.get(n, Ellipsis) for n in names)
        if any(
            type(v) not in (bool, int, str, type(None))
            or (type(v) is str and len(v) > 64)
            for v in values
        ):
            saturated.add(index)
            return
        cache = memo[index]
        key = tuple((type(v), v) for v in values)
        code = cache.get(key)
        if code is None:
            try:
                code = sum(
                    1 << j
                    for j, atom in enumerate(atoms)
                    if eval(atom, {"__builtins__": {}}, env)
                )
            except (TypeError, ValueError, NameError):
                saturated.add(index)
                return
            if len(cache) < 512:
                cache[key] = code
        record(code, index)

    builtins._br_guard = guard

    class Rewrite(ast.NodeTransformer):
        def visit_BoolOp(self, node):
            if not boolean or not guards.supported(node):
                return self.generic_visit(node)
            i = len(sites)
            sites.append(guards.site(self.path, node))
            atoms = guards.leaves(node)
            descriptions[i] = (
                sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)}),
                [compile(ast.Expression(a), self.path, "eval") for a in atoms],
            )
            memo[i] = {}
            mark = ast.Call(
                ast.Name("_br_guard", ast.Load()),
                [ast.Constant(i), ast.Call(ast.Name("locals", ast.Load()), [], [])],
                [],
            )
            return ast.copy_location(
                ast.Subscript(
                    ast.Tuple([mark, node], ast.Load()), ast.Constant(1), ast.Load()
                ),
                node,
            )

        def visit_Compare(self, node):
            if boolean:
                return self.generic_visit(node)
            self.generic_visit(node)
            if len(node.ops) == 1 and literal(node.comparators[0]):
                i = len(sites)
                sites.append(key(self.path, node))
                node.left = ast.copy_location(
                    ast.Call(
                        ast.Name("_br_value", ast.Load()),
                        [node.left, ast.Constant(i)],
                        [],
                    ),
                    node.left,
                )
            return node

    original = importlib.machinery.SourceFileLoader.get_code

    def get_code(loader, fullname):
        if not loader.path.startswith(str(source / "sqlglot") + "/"):
            return original(loader, fullname)
        tree = ast.parse(loader.get_data(loader.path), loader.path)
        rewrite = Rewrite()
        rewrite.path = os.path.relpath(loader.path, source)
        return compile(
            ast.fix_missing_locations(rewrite.visit(tree)),
            loader.path,
            "exec",
            dont_inherit=True,
        )

    importlib.machinery.SourceFileLoader.get_code = get_code
    masks = defaultdict(dict)
    groups = defaultdict(list)
    for t in tests:
        path, cls, method = t["id"].split("::")
        groups[path, cls].append((t["id"], method))
    names = []
    for (path, cls), members in groups.items():
        print(path, flush=True)
        klass = getattr(importlib.import_module(path[:-3].replace("/", ".")), cls)
        seen.clear()
        saturated.clear()
        klass.setUpClass()
        shared = {i: s.copy() for i, s in seen.items()}
        unknown = saturated.copy()
        for name, method in members:
            seen.clear()
            saturated.clear()
            result = unittest.TestResult()
            klass(method).run(result)
            bit = 1 << len(names)
            names.append(name)
            for i in seen.keys() | shared.keys() | saturated | unknown:
                table = masks[sites[i]]
                if (
                    i in saturated
                    or i in unknown
                    or not result.wasSuccessful()
                    or result.skipped
                ):
                    table["?"] = table.get("?", 0) | bit
                else:
                    for val in seen.get(i, set()) | shared.get(i, set()):
                        v = json.dumps(val, separators=(",", ":"))
                        table[v] = table.get(v, 0) | bit
        klass.tearDownClass()
        klass.doClassCleanups()
    save_map(output, names, masks, {}, {})


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:4]), boolean="--guards" in sys.argv[4:])
