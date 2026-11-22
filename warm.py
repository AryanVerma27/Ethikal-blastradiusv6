"""Share the pristine SQLGlot import across isolated, patched child processes."""

import concurrent.futures
import gc
import importlib
import importlib.machinery
from functools import lru_cache
import sys
import types
from pathlib import Path
from build_map import InlinePool

_root = None
_loaded = set()
_called = set()
_functions = {}


def prepare(source):
    global _root
    if _root is not None:
        return
    _root = str(source) + "/"
    sys.path.insert(0, str(source))
    concurrent.futures.ProcessPoolExecutor = InlinePool

    monitor = sys.monitoring

    def seen(code, offset):
        if code.co_filename.startswith(_root):
            _called.add((code.co_filename, code.co_qualname, code.co_firstlineno))
        return monitor.DISABLE

    monitor.use_tool_id(3, "clean import dependencies")
    monitor.register_callback(3, monitor.events.PY_START, seen)
    monitor.set_events(3, monitor.events.PY_START)
    try:
        for name in (
            "sqlglot",
            "tests.helpers",
            "tests.test_executor",
            "tests.test_optimizer",
            "tests.test_parser",
            "tests.dialects.test_dialect",
        ):
            importlib.import_module(name)
    finally:
        monitor.set_events(3, 0)
        monitor.register_callback(3, monitor.events.PY_START, None)
        monitor.free_tool_id(3)
    for module in tuple(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if isinstance(path, str) and path.startswith(_root):
            _loaded.add(path)
    for obj in gc.get_objects():
        if type(obj) is types.FunctionType:
            code = obj.__code__
            if code.co_filename in _loaded:
                _functions.setdefault(
                    (code.co_filename, code.co_qualname, code.co_firstlineno), []
                ).append(obj)


def replacements_for(old, new):
    # A changed import, class attribute, default argument, or decorator can
    # affect import-time state. Require a fresh interpreter in those cases.
    shape = (
        "co_code",
        "co_names",
        "co_varnames",
        "co_freevars",
        "co_cellvars",
        "co_flags",
        "co_argcount",
        "co_kwonlyargcount",
        "co_posonlyargcount",
        "co_exceptiontable",
    )

    def visit(a, b):
        nested = []
        changed = any(getattr(a, k) != getattr(b, k) for k in shape) or len(
            a.co_consts
        ) != len(b.co_consts)
        if not changed:
            for x, y in zip(a.co_consts, b.co_consts):
                if (
                    type(x) is types.CodeType
                    and type(y) is types.CodeType
                    and x.co_qualname == y.co_qualname
                ):
                    nested.extend(visit(x, y))
                elif type(x) is not type(y) or x != y:
                    changed = True
                    break
        if changed or (nested and a.co_flags & 1):
            if not a.co_flags & 1:
                raise ValueError("import-time change")
            key = a.co_filename, a.co_qualname, a.co_firstlineno
            if key in _called or a.co_freevars != b.co_freevars:
                raise ValueError("preload depends on changed code")
            return [(key, b)]
        return nested

    return visit(old, new)


@lru_cache(maxsize=64)
def baseline_code(path):
    # Read the bytecode compiled by the image build. Use the upstream path as
    # the filename so the nested function keys match the preloaded functions.
    return importlib.machinery.SourceFileLoader("_br_pristine", path).get_code(
        "_br_pristine"
    )


def apply(replacements):
    pending = []
    for path, content in replacements.items():
        if path not in _loaded:
            continue
        try:
            old = baseline_code(path)
            new = compile(content, path, "exec", dont_inherit=True)
            for key, code in replacements_for(old, new):
                if key not in _functions:
                    return False
                pending.extend((fn, code) for fn in _functions[key])
        except (SyntaxError, ValueError):
            return False
    for fn, code in pending:
        fn.__code__ = code
    return True
