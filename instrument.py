"""Track the provenance of class configuration and parser dispatch callbacks."""

import ast
import builtins
import functools
import importlib.machinery
import types
import enum
from collections import defaultdict


def install(root, hits):
    observed = defaultdict(set)
    type_cache = {}

    def observe(value, site):
        cls = type(value)
        if cls not in type_cache:
            if issubclass(cls, enum.Enum) and cls.__eq__ in (
                str.__eq__,
                object.__eq__,
                int.__eq__,
            ):
                category = "enum:" + cls.__name__
            elif cls.__eq__ is object.__eq__ or cls.__module__ == "builtins":
                category = "ordinary"
            else:
                category = "custom"
            type_cache[cls] = category
        observed[site].add(type_cache[cls])
        return value

    class LiteralDict(dict):
        def __init__(self, values, site):
            super().__init__(values)
            self.site = site

        def touch(self, key):
            if type(key) is str and key in self:
                hits.add((self.site + "/" + key, 1))

        def __getitem__(self, key):
            self.touch(key)
            return super().__getitem__(key)

        def get(self, key, default=None):
            self.touch(key)
            return super().get(key, default)

        def items(self):
            hits.add((self.site, 1))
            return super().items()

        def keys(self):
            hits.add((self.site, 1))
            return super().keys()

        def __iter__(self):
            hits.add((self.site, 1))
            return super().__iter__()

        def values(self):
            hits.add((self.site, 1))
            return super().values()

        def copy(self):
            hits.add((self.site, 1))
            return super().copy()

    builtins._blast_literal_dict = LiteralDict
    builtins._blast_observe = observe
    owners = {}
    attr_cache = {}

    def get_attr(obj, key):
        value = getattr(obj, key)
        cls = obj if isinstance(obj, type) else type(obj)
        cache_key = cls, key
        if cache_key not in attr_cache:
            attr_cache[cache_key] = next(
                (
                    owners[(base.__module__, base.__qualname__, key)]
                    for base in cls.__mro__
                    if (base.__module__, base.__qualname__, key) in owners
                ),
                None,
            )
        place = attr_cache[cache_key]
        if place:
            hits.add(place)
        return value

    def tag(value, path, line):
        if not isinstance(value, types.FunctionType):
            return value

        @functools.wraps(value)
        def dispatch(*args, **kwargs):
            hits.add((path, line))
            return value(*args, **kwargs)

        return dispatch

    builtins._blast_attr = get_attr
    builtins._blast_tag = tag
    original = importlib.machinery.SourceFileLoader.get_code

    class Rewrite(ast.NodeTransformer):
        def visit_Compare(self, node):
            right = node.comparators[0]
            self.generic_visit(node)
            if len(node.ops) == 1 and isinstance(node.ops[0], (ast.Is, ast.IsNot)):
                if isinstance(right, ast.Constant) or (
                    isinstance(right, ast.Attribute) and right.attr.isupper()
                ):
                    node.left = ast.copy_location(
                        ast.Call(
                            ast.Name("_blast_observe", ast.Load()),
                            [node.left, ast.Constant(f"{self.path}:{node.lineno}")],
                            [],
                        ),
                        node.left,
                    )
            return node

        def visit_Attribute(self, node):
            self.generic_visit(node)
            if isinstance(node.ctx, ast.Load) and (
                node.attr.isupper() or node.attr == "arg_types"
            ):
                return ast.copy_location(
                    ast.Call(
                        ast.Name("_blast_attr", ast.Load()),
                        [node.value, ast.Constant(node.attr)],
                        [],
                    ),
                    node,
                )
            return node

        def visit_ClassDef(self, node):
            self.generic_visit(node)
            for stmt in node.body:
                if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or not isinstance(
                    stmt.value, ast.Dict
                ):
                    continue
                try:
                    values = ast.literal_eval(stmt.value)
                except (ValueError, TypeError):
                    continue
                if values and all(type(k) is str for k in values):
                    site = f"{self.path}:{stmt.value.lineno}:dict"
                    observed[site].add("mapped")
                    stmt.value = ast.copy_location(
                        ast.Call(
                            ast.Name("_blast_literal_dict", ast.Load()),
                            [stmt.value, ast.Constant(site)],
                            [],
                        ),
                        stmt.value,
                    )
            return node

        def visit_Dict(self, node):
            self.generic_visit(node)
            for i, val in enumerate(node.values):
                if isinstance(val, ast.Call) and isinstance(val.func, ast.Name):
                    node.values[i] = ast.copy_location(
                        ast.Call(
                            ast.Name("_blast_tag", ast.Load()),
                            [val, ast.Constant(self.path), ast.Constant(val.lineno)],
                            [],
                        ),
                        val,
                    )
            return node

    def get_code(loader, name):
        if not loader.path.startswith(str(root / "sqlglot") + "/"):
            return original(loader, name)
        path = loader.path[len(str(root)) + 1 :]
        tree = ast.parse(loader.get_data(loader.path), filename=loader.path)

        def classes(body, prefix):
            for cls in body:
                if isinstance(cls, ast.ClassDef):
                    qual = prefix + cls.name
                    for stmt in cls.body:
                        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                            targets = (
                                stmt.targets
                                if isinstance(stmt, ast.Assign)
                                else [stmt.target]
                            )
                            for target in targets:
                                if isinstance(target, ast.Name):
                                    owners[name, qual, target.id] = path, stmt.lineno
                    classes(cls.body, qual + ".")

        classes(tree.body, "")
        rewrite = Rewrite()
        rewrite.path = path
        return compile(
            ast.fix_missing_locations(rewrite.visit(tree)),
            loader.path,
            "exec",
            dont_inherit=True,
        )

    importlib.machinery.SourceFileLoader.get_code = get_code
    return observed
