"""Describe expression blocks whose execution depends on an unchanged guard."""

import ast


def guarded(node):
    if isinstance(node, ast.IfExp):
        return [("body", None), ("orelse", None)]
    if isinstance(node, ast.BoolOp):
        return [("values", i) for i in range(1, len(node.values))]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return [("elt", None)]
    if isinstance(node, ast.DictComp):
        return [("key", None), ("value", None)]
    return []


def get(node, field, i):
    value = getattr(node, field)
    return value if i is None else value[i]


def put(node, field, i, value):
    if i is None:
        setattr(node, field, value)
    else:
        getattr(node, field)[i] = value


def site(path, node, field, i):
    return f"{path}:{node.lineno}:{node.col_offset}:{type(node).__name__}:{field}:{i}"
