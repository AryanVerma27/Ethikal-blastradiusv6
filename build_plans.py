"""Record what the pinned Python executor receives for each clean TPC query."""

import hashlib
import json
import pickle
import sys
from pathlib import Path
from unittest.mock import patch
from cases import TABLES
from engine import execution_key


def main(source, output):
    sys.path.insert(0, str(source.resolve()))
    import tests.test_executor as tests
    from sqlglot.executor.python import PythonExecutor
    from table_fixtures import prepare

    klass = tests.TestExecutor
    prepare(klass)
    keys = {}
    try:
        for name, (attr, _) in TABLES.items():
            if "test_execute_" not in name:
                continue
            dataset = "tpch" if name.endswith("_tpch") else "tpcds"
            tests.initializer(
                getattr(tests, dataset.upper() + "_SCHEMA"),
                getattr(klass, dataset + "_tables"),
            )
            for i, (meta, sql, _) in enumerate(getattr(klass, attr)):
                if not meta.get("execute"):
                    continue
                captured = []

                def capture(self, plan):
                    captured.append(execution_key(plan))

                expression = pickle.loads(pickle.dumps(tests.parse_one(sql)))
                with patch.object(PythonExecutor, "execute", capture):
                    tests.mp_execute(expression, meta)
                if len(captured) == 1:
                    keys[f"{name}/{i}"] = captured[0]
    finally:
        klass.tearDownClass()
    output.write_text(json.dumps(keys, separators=(",", ":")))


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
