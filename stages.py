"""Replay SQLGlot transformations at the input to expensive TPC execution."""

import hashlib
import base64
import zlib
import json
import pickle


from functools import lru_cache
import importlib


def encode_expr(value):
    from sqlglot import exp

    expr_type, dtype = exp.Expr, exp.DType

    def encode(v):
        if isinstance(v, expr_type):
            cls = type(v)
            return [
                "node",
                cls.__module__,
                cls.__qualname__,
                [[k, encode(arg)] for k, arg in v.args.items()],
                "self" if v._type is v else encode(v._type),
                v.comments,
                v._meta,
            ]
        if isinstance(v, dtype):
            return ["dtype", v.value]
        if isinstance(v, list):
            return ["list", [encode(arg) for arg in v]]
        return v

    return encode(value)


@lru_cache(maxsize=None)
def expression_class(module, name):
    return getattr(importlib.import_module(module), name)


def decode_expr(value):
    if not isinstance(value, list):
        return value
    if value[0] == "dtype":
        from sqlglot import exp

        return exp.DType(value[1])
    if value[0] == "list":
        return [decode_expr(v) for v in value[1]]
    _, module, cls, args, typ, comments, meta = value
    node = expression_class(module, cls)(**{k: decode_expr(v) for k, v in args})
    node._type = node if typ == "self" else decode_expr(typ)
    node.comments = comments
    node._meta = meta
    return node


def plan_state(plan, execution_only=False):
    from sqlglot import exp
    from sqlglot.planner import Step

    def encode(value):
        if isinstance(value, Step):
            fields = {
                k: encode(v)
                for k, v in vars(value).items()
                if k not in ("dependencies", "dependents")
            }
            children = sorted(
                (encode(c) for c in value.dependencies),
                key=lambda r: json.dumps(r, sort_keys=True),
            )
            return [type(value).__name__, fields, children]
        if isinstance(value, exp.Expr):
            return encode_expr(value)
        if isinstance(value, dict):
            return [[encode(k), encode(v)] for k, v in value.items()]
        if isinstance(value, (tuple, list)):
            return [encode(v) for v in value]
        if isinstance(value, type):
            return value.__qualname__
        return value

    return (
        encode(plan.root)
        if execution_only
        else [encode(plan.root), encode(plan.expression)]
    )


def signature(stage, value):
    if stage in ("parse", "optimize"):
        value = encode_expr(value)
    if stage == "plan":
        value = plan_state(value)
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def verify(
    name, checks, snapshots, on_difference=None, executable=None, locations=None
):
    from sqlglot import parse_one, transpile
    from sqlglot.executor import optimize
    from sqlglot.schema import ensure_schema
    from tests.helpers import TPCH_SCHEMA, TPCDS_SCHEMA, load_sql_fixture_pairs
    from cases import TABLES

    filename = TABLES[name][1].removeprefix("tests/fixtures/")
    inputs = list(load_sql_fixture_pairs(filename))
    schema = TPCH_SCHEMA if name.endswith("_tpch") else TPCDS_SCHEMA
    different = set()
    for index, stage, expected in checks:
        if index in different:
            continue
        if stage == "optimize" and locations:
            import checkpoints

            if checkpoints.unchanged(name, index, locations, expected=expected):
                continue
        sql = inputs[index][1]
        if stage == "plan":
            from sqlglot.planner import Plan

            value = Plan(
                decode_expr(
                    json.loads(zlib.decompress(base64.b64decode(snapshots[str(index)])))
                )
            )
        elif stage == "reference":
            value = transpile(sql, write="duckdb")
        else:
            value = parse_one(sql)
            if stage == "optimize":
                value = pickle.loads(pickle.dumps(value))
                value = optimize(
                    value,
                    ensure_schema(schema, dialect=None),
                    leave_tables_isolated=True,
                    dialect=None,
                )
        if signature(stage, value) != expected:
            if stage == "optimize" and executable and index in executable:
                from sqlglot.planner import Plan
                from engine import execution_key

                if execution_key(Plan(value)) == executable[index]:
                    continue
            if on_difference is not None and not different:
                on_difference(index)
            different.add(index)
            if len(different) > 3:
                return None
    return sorted(different)
