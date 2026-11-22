import json
import os
from pathlib import Path
import sys
import unittest
from stress import exercise, SCALES
from instrument import install


def main(source, output):
    source, output = source.resolve(), output.resolve()
    os.chdir(source)
    sys.path.insert(0, str(source))
    hits = set()
    install(source, hits)
    from tests.test_parser import TestParser

    prefix = str(source) + "/"
    mon = sys.monitoring

    def line(code, n):
        if code.co_filename.startswith(prefix + "sqlglot/") and code.co_flags & 1:
            hits.add((code.co_filename[len(prefix) :], n))
        return mon.DISABLE

    mon.use_tool_id(3, "stress phases")
    mon.register_callback(3, mon.events.LINE, line)
    mon.set_events(3, mon.events.LINE)
    measures = []
    for width in (None, *SCALES):
        hits.clear()
        mon.restart_events()
        if width is None:
            result = unittest.TestResult()
            TestParser("test_max_nodes").run(result)
            assert result.wasSuccessful()
        else:
            assert exercise(TestParser, width)
        measures.append(hits.copy())
    mon.set_events(3, 0)
    gaps = {}
    for path, n in measures[0] - (measures[1] & measures[2]):
        gaps.setdefault(path, []).append(n)
    output.write_text(
        json.dumps({p: sorted(v) for p, v in gaps.items()}, separators=(",", ":"))
    )


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
