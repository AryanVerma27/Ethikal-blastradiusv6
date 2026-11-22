"""Run the actual test with unaffected standalone cases filtered out."""

import contextlib
import importlib
import sys
import unittest
from unittest import mock
from calls import key_for, METHODS


def run(klass, name, kind, spec):
    module = importlib.import_module(klass.__module__)
    method = name.split("::")[-1]
    with contextlib.ExitStack() as stack:
        if kind == "rows":
            original = module.load_sql_fixture_pairs

            def filtered(path):
                indices = spec.get(path)
                for i, row in enumerate(original(path)):
                    if indices is None or i in indices:
                        yield row

            stack.enter_context(
                mock.patch.object(module, "load_sql_fixture_pairs", filtered)
            )
        elif kind == "calls":
            from tests.dialects.test_dialect import Validator

            def wrap(fn):
                def invoke(self, *args, **kwargs):
                    caller = sys._getframe(1)
                    if caller.f_code.co_name == method:
                        key = key_for(fn.__name__, caller.f_lineno, args, kwargs)
                        if key in spec:
                            return None
                    return fn(self, *args, **kwargs)

                return invoke

            stack.enter_context(
                mock.patch.multiple(
                    Validator, **{k: wrap(getattr(Validator, k)) for k in METHODS}
                )
            )
        result = unittest.TestResult()
        result.failfast = True
        klass(method).run(result)
        return result.wasSuccessful() and not result.skipped
