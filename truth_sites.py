"""Pure observations supported by the Boolean condition profiler."""

import ast


def atom_ok(node):
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Compare):
        return (
            len(node.ops) == 1
            and isinstance(node.ops[0], (ast.Is, ast.IsNot))
            and isinstance(node.left, ast.Name)
            and (
                isinstance(node.comparators[0], ast.Name)
                or isinstance(node.comparators[0], ast.Constant)
                and node.comparators[0].value in (None, True, False)
            )
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and len(node.args) == 2
        and not node.keywords
        and isinstance(node.args[0], ast.Name)
    ):
        types = (
            node.args[1].elts if isinstance(node.args[1], ast.Tuple) else [node.args[1]]
        )
        return all(
            isinstance(t, ast.Name)
            or isinstance(t, ast.Attribute)
            and isinstance(t.value, ast.Name)
            and t.value.id == "exp"
            for t in types
        )
    return False


def supported(node):
    from guards import leaves

    return (
        isinstance(node, ast.BoolOp)
        and len(leaves(node)) <= 5
        and all(atom_ok(a) for a in leaves(node))
    )


from functools import lru_cache
from pathlib import Path
import marshal
from zipfile import ZipFile


@lru_cache(maxsize=1)
def archive():
    return ZipFile(Path(__file__).with_name("truth.zip"))


@lru_cache(maxsize=16)
def tables(path):
    try:
        return marshal.loads(archive().read(path))
    except KeyError:
        return {}


def exclusions(path, original, changed, removed, same):
    import guards

    if not removed:
        return 0
    for a, b in zip(ast.walk(original), ast.walk(changed)):
        if type(a) != type(b) or not isinstance(a, (ast.If, ast.While, ast.IfExp)):
            continue
        old, new = a.test, b.test
        if (
            not (old.lineno <= min(removed) and max(removed) <= old.end_lineno)
            or not supported(old)
            or same(old, new)
        ):
            continue
        atoms = [ast.dump(n) for n in guards.leaves(old)]
        if any(ast.dump(n) not in atoms for n in guards.leaves(new)):
            continue
        # An identity comparison against an integer literal can depend on the
        # compiler's constant interning. Use singleton constants only.
        if any(
            isinstance(n, ast.Compare)
            and isinstance(n.comparators[0], ast.Constant)
            and type(n.comparators[0].value) not in (bool, type(None))
            for n in guards.leaves(old)
        ):
            continue
        # Local type aliases and shadowed builtins would change isinstance's
        # meaning. Require the names resolved from globals when profiling.
        sensitive = {
            x.id
            for n in guards.leaves(old)
            if isinstance(n, ast.Call)
            for x in ast.walk(n.args[1])
            if isinstance(x, ast.Name)
        } | {"isinstance"}
        if any(
            isinstance(n, ast.arg)
            and n.arg in sensitive
            or isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Store)
            and n.id in sensitive
            for n in ast.walk(original)
        ):
            continue
        b.test = old
        try:
            isolated = same(original, changed)
        finally:
            b.test = new
        if not isolated:
            continue
        records = tables(path).get(guards.site(path, old), {})
        diverges = 0
        for pattern in range(1 << len(atoms)):
            truth = {key: bool(pattern & (1 << i)) for i, key in enumerate(atoms)}
            if guards.evaluate(old, truth) != guards.evaluate(new, truth):
                diverges |= 1 << pattern
        excluded = 0
        for patterns, bits in records.items():
            if not patterns & diverges:
                excluded |= bits
        return excluded
    return 0
