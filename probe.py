"""Check selected tests or independently profiled iterations with a bounded fork."""

import builtins
import concurrent.futures
import importlib
import importlib.machinery
import io
import pickle
import tempfile
import warm
import engine
import table_fixtures
import rule_replay
import fragments
import stress
from boundary import replay
from stages import verify
import os
import signal
import sys
import time
import unittest
from cases import fixture_cases, fixture_status, run_cases


class FailedQuery(Exception):
    pass


def run(source, replacements, jobs, deadline, passed, anchors=()):
    original_code = importlib.machinery.SourceFileLoader.get_code

    def get_code(loader, fullname):
        if loader.path in replacements:
            return compile(
                replacements[loader.path], loader.path, "exec", dont_inherit=True
            )
        return original_code(loader, fullname)

    original_open = builtins.open

    def fixture_open(path, mode="r", *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and "r" in mode:
            filename = os.path.abspath(path)
            if filename.endswith(".sql") and filename in replacements:
                text = replacements[filename]
                return io.BytesIO(text.encode()) if "b" in mode else io.StringIO(text)
        return original_open(path, mode, *args, **kwargs)

    importlib.machinery.SourceFileLoader.get_code = get_code
    builtins.open = fixture_open
    os.chdir(source)
    sys.path.insert(0, str(source))
    from build_map import InlinePool

    concurrent.futures.ProcessPoolExecutor = InlinePool
    groups = {}
    # Read the clean rule inputs only if the matching test and module were
    # fully profiled. An inconclusive replay resumes the existing test plan.
    jobs = list(jobs)
    proved = set()
    for i, (name, plan) in enumerate(jobs):
        if isinstance(plan, dict) and "checkpoint" in plan:
            import checkpoints

            try:
                if checkpoints.unchanged(
                    name,
                    -1,
                    plan["checkpoint"],
                    min(deadline, time.process_time() + 0.45),
                ):
                    passed.append(i)
                    proved.add(i)
            except Exception:
                pass
            jobs[i] = (name, plan["fallback"])
            continue
        if isinstance(plan, dict) and "rules" in plan:
            try:
                verdict = rule_replay.verify_rules(
                    name,
                    plan["rules"],
                    source,
                    min(deadline, time.process_time() + 0.7),
                )
                if verdict is not None:
                    proved.add(i)
                    if verdict:
                        passed.append(i)
                    elif name in anchors:
                        return
            except Exception:
                pass
            jobs[i] = (name, plan["fallback"])
    fallback = {}
    failed_preflight = set(proved)
    for i, (name, plan) in enumerate(jobs):
        if (
            isinstance(plan, list)
            and replacements
            and all(f.endswith(".sql") for f in replacements)
        ):
            try:
                status = fixture_status(name, plan, source, replacements)
            except Exception:
                continue
            if status is not None:
                failed_preflight.add(i)
                if status:
                    passed.append(i)

    # Every executor query is parsed and serialized before the worker can
    # return a result. A failure here retains the test and saves its replay.
    for index, (name, plan) in enumerate(jobs):
        if index in failed_preflight or not name.startswith(
            "tests/test_executor.py::TestExecutor::test_execute_tpc"
        ):
            continue
        if time.process_time() >= deadline:
            break
        try:
            from sqlglot import parse_one
            from tests.helpers import load_sql_fixture_pairs
            from cases import TABLES

            filename = TABLES[name][1].removeprefix("tests/fixtures/")
            rows = list(load_sql_fixture_pairs(filename))
            first = plan[0] if isinstance(plan, list) and plan else 0
            pickle.loads(pickle.dumps(parse_one(rows[first][1])))
        except Exception:
            if name in anchors:
                return
            failed_preflight.add(index)

    for index, (name, plan) in enumerate(jobs):
        if (
            index in failed_preflight
            or not isinstance(plan, dict)
            or time.process_time() >= deadline
        ):
            continue
        try:
            if "sql_outputs" in plan:
                import sql_outputs
                from cases import TABLES, OPT_FILES
                from tests.helpers import load_sql_fixture_pairs

                filename = OPT_FILES.get(name) or TABLES[name][1]
                total = sum(
                    1
                    for _ in load_sql_fixture_pairs(
                        filename.removeprefix("tests/fixtures/")
                    )
                )
                changed = sql_outputs.residual(name, range(total), plan["sql_outputs"])
                if not changed:
                    passed.append(index)
                elif len(changed) <= 12:
                    fallback[index] = changed
            elif "stress" in plan:
                from tests.test_parser import TestParser

                if all(stress.exercise(TestParser, w) for w in stress.SCALES):
                    passed.append(index)
            elif "parse_calls" in plan:
                changed = replay(plan["parse_calls"], plan["positions"], name)
                if changed == []:
                    passed.append(index)
                elif changed:
                    checks = [
                        row
                        for row in plan.get("query_engine", [])
                        if row[0] in changed and not row[3]
                    ]
                    if checks:
                        checked = {row[0] for row in checks}
                        changed = sorted(
                            (set(changed) - checked) | set(engine.check(name, checks))
                        )
                    if changed:
                        fallback[index] = changed
                    else:
                        passed.append(index)
            elif "stages" in plan or "engine" in plan:
                residual = None
                if "stages" in plan:

                    def witness(query):
                        from tests.test_executor import TestExecutor

                        if not table_fixtures.CACHE.is_dir():
                            return
                        table_fixtures.prepare(TestExecutor)
                        try:
                            rows = fixture_cases(TestExecutor, name)
                            if not run_cases(TestExecutor, name, rows, [query]):
                                raise FailedQuery()
                        finally:
                            TestExecutor.tearDownClass()

                    executable = {
                        row[0]: engine.execution_keys().get(f"{name}/{row[0]}")
                        for row in plan.get("engine", [])
                        if not row[3]
                    }
                    residual = verify(
                        name,
                        plan["stages"],
                        plan["inputs"],
                        witness,
                        executable,
                        plan.get("locations"),
                    )
                if "engine" in plan and residual != []:
                    checks = plan["engine"]
                    if residual is not None:
                        checks = [item for item in checks if item[0] in residual]
                    residual = engine.check(name, checks)
                if residual == []:
                    passed.append(index)
                elif residual:
                    fallback[index] = residual
        except FailedQuery:
            if name in anchors:
                return
            continue
        except Exception:
            continue
    for index, (name, cases) in enumerate(jobs):
        if index in failed_preflight:
            continue
        if isinstance(cases, dict) and not ("rows" in cases or "calls" in cases):
            if index not in fallback:
                continue
            cases = fallback[index]
        path, cls, method = name.split("::")
        groups.setdefault((path, cls), []).append((index, name, method, cases))
    for (path, cls), members in groups.items():
        klass = getattr(importlib.import_module(path[:-3].replace("/", ".")), cls)
        setup_skipped = False
        if (
            path == "tests/test_executor.py"
            and all(plan is None for _, _, _, plan in members)
            and all("/sqlglot/" in f and "/executor/" not in f for f in replacements)
        ):
            # These upstream methods operate solely on local tables. Check the
            # transitive method bodies for any class fixture reads before eliding setup.
            import inspect
            import ast
            import textwrap

            fixture_attrs = {
                "tpch_conn",
                "tpcds_conn",
                "tpch_tables",
                "tpcds_tables",
                "cache",
                "tpch_sqls",
                "tpcds_sqls",
            }
            seen = set()

            def local_method(fn):
                if fn in seen:
                    return True
                seen.add(fn)
                for n in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
                    if (
                        isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name)
                        and n.value.id in ("self", "cls")
                    ):
                        if n.attr in fixture_attrs:
                            return False
                        child = klass.__dict__.get(n.attr)
                        if inspect.isfunction(child) and not local_method(child):
                            return False
                return True

            setup_skipped = all(
                local_method(getattr(klass, m)) for _, _, m, _ in members
            )
        cacheable = (
            path == "tests/test_executor.py"
            and table_fixtures.CACHE.is_dir()
            and all(
                m in ("test_execute_tpch", "test_execute_tpcds", "test_optimized_tpch")
                for _, _, m, _ in members
            )
            and all(
                ("/sqlglot/" in f and "/executor/" not in f)
                or f.endswith(("/tpc-h.sql", "/tpc-ds.sql"))
                for f in replacements
            )
        )
        if cacheable:
            table_fixtures.prepare(klass)
        elif not setup_skipped:
            klass.setUpClass()
        try:
            for index, name, method, cases in members:
                if time.process_time() >= deadline:
                    return
                if cases is None:
                    result = unittest.TestResult()
                    result.failfast = True
                    klass(method).run(result)
                    ok = result.wasSuccessful() and not result.skipped
                elif isinstance(cases, dict):
                    kind = next(iter(cases))
                    ok = fragments.run(klass, name, kind, cases[kind])
                else:
                    items = fixture_cases(klass, name)
                    if name.startswith("tests/test_executor."):
                        ok = all(run_cases(klass, name, items, [i]) for i in cases)
                    else:
                        ok = run_cases(klass, name, items, cases)
                if ok:
                    passed.append(index)
                elif name in anchors:
                    return
        finally:
            if cacheable or not setup_skipped:
                klass.tearDownClass()
            klass.doClassCleanups()


class Passed:
    def __init__(self, writer):
        self.writer = writer

    def append(self, index):
        os.write(self.writer, f"{index} ".encode())


def check(source, replacements, jobs, timeout, anchors=()):
    # Share immutable baseline metadata among the commit-specific children.
    # The actual SQL/optimizer calls and their results stay in each child.
    for _, plan in jobs:
        if not isinstance(plan, dict):
            continue
        if "parse_calls" in plan:
            from boundary import fixed_digests, query_rows

            fixed_digests()
            query_rows()
        if "engine" in plan or "query_engine" in plan:
            engine.execution_keys()
    warm.prepare(source)
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        try:
            signal.setitimer(signal.ITIMER_REAL, 2.5 * timeout + 1)
            signal.setitimer(signal.ITIMER_PROF, timeout)
            with open(os.devnull, "wb") as sink:
                os.dup2(sink.fileno(), 1)
                os.dup2(sink.fileno(), 2)
            deadline = time.process_time() + timeout * 0.95
            if not warm.apply(replacements):
                with tempfile.TemporaryFile() as handle:
                    pickle.dump((source, replacements, jobs, deadline, anchors), handle)
                    handle.seek(0)
                    os.set_inheritable(handle.fileno(), True)
                    os.set_inheritable(writer, True)
                    os.execv(
                        sys.executable,
                        [sys.executable, __file__, str(handle.fileno()), str(writer)],
                    )
            run(source, replacements, jobs, deadline, Passed(writer), anchors)
        except Exception:
            pass
        finally:
            os._exit(0)
    os.close(writer)
    with os.fdopen(reader) as stream:
        answer = stream.read()
    os.waitpid(pid, 0)
    return {jobs[int(i)][0] for i in answer.split()}


if __name__ == "__main__":
    with os.fdopen(int(sys.argv[1]), "rb") as stream:
        source, replacements, jobs, deadline, anchors = pickle.load(stream)
    run(source, replacements, jobs, deadline, Passed(int(sys.argv[2])), anchors)
