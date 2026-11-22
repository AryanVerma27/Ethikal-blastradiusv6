"""Describe the shape and truth values of scalar Boolean predicates."""

import ast


def leaves(node):
    if isinstance(node, ast.BoolOp):
        return [leaf for child in node.values for leaf in leaves(child)]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return leaves(node.operand)
    return [node]


def evaluate(node, truth):
    if isinstance(node, ast.BoolOp):
        combine = all if isinstance(node.op, ast.And) else any
        return combine(evaluate(child, truth) for child in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not evaluate(node.operand, truth)
    return truth[ast.dump(node)]


def supported(node):
    if not isinstance(node, ast.BoolOp):
        return False
    atoms = leaves(node)
    if len(atoms) > 6:
        return False
    allowed = (
        ast.Name,
        ast.Load,
        ast.Compare,
        ast.Constant,
        ast.Tuple,
        ast.List,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
    )
    return all(isinstance(part, allowed) for atom in atoms for part in ast.walk(atom))


def site(path, node):
    return f"{path}:{node.lineno}:{node.col_offset}"
