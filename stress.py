"""A bounded stress prediction for size-independent SQL grammar edits."""

import ast
import inspect
import re
import textwrap
import unittest
from unittest import mock

NAME = "tests/test_parser.py::TestParser::test_max_nodes"
SCALES = (64, 192)
SENSITIVE = re.compile(
    r"(limit|count|length|size|depth|width|offset|index|position|memory|recurs|error|_chunks|_advance|self\.sql|len\s*\(|\brange\s*\()",
    re.I,
)


def checkable(files):
    for path, edits in files.items():
        if not path.startswith("sqlglot/") or not path.endswith(".py"):
            return False
        for _, _, line in edits:
            if SENSITIVE.search(line) or re.search(
                r"\b(while|for|raise|yield|def|class)\b", line
            ):
                return False
    return True


def scaled_method(original, width):
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))

    class Scale(ast.NodeTransformer):
        def visit_Constant(self, node):
            if type(node.value) is int:
                values = {10000: width, 1_000_000: width * 100, 50_000: width * 5}
                if node.value in values:
                    return ast.copy_location(ast.Constant(values[node.value]), node)
            if node.value == "(50000)":
                return ast.copy_location(ast.Constant(f"({width*5})"), node)
            return node

    ns = {}
    exec(
        compile(Scale().visit(tree), original.__code__.co_filename, "exec"),
        original.__globals__,
        ns,
    )
    return ns[original.__name__]


def exercise(klass, width):
    original = klass.test_max_nodes
    with mock.patch.object(klass, "test_max_nodes", scaled_method(original, width)):
        result = unittest.TestResult()
        result.failfast = True
        klass("test_max_nodes").run(result)
        return result.wasSuccessful() and not result.skipped
