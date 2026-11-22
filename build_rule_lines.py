"""Measure the lines reached by each verified baseline optimizer-rule replay."""

from pathlib import Path
import json
import sys
import zlib
from rule_replay import catalog, same_output


def build(source, output):
    sys.path.insert(0, str(source.resolve()))
    modules = {}
    records = {}
    for tests in catalog().values():
        for mod, entries in tests.items():
            modules[str(source.resolve() / (mod.replace(".", "/") + ".py"))] = mod
            for row in entries:
                records.setdefault(tuple(row), set()).add(mod)
    trace = sys.monitoring
    trace.use_tool_id(4, "optimizer rule lines")
    hits = {}

    def line(code, number):
        module = modules.get(code.co_filename)
        if module:
            hits.setdefault(module, set()).add(number)
        return trace.DISABLE

    trace.register_callback(4, trace.events.LINE, line)
    trace.set_events(4, trace.events.LINE)
    result = {}
    for entry, mods in records.items():
        hits.clear()
        trace.restart_events()
        try:
            valid = same_output(*entry)
        except Exception:
            valid = False
        if valid:
            result["|".join(entry)] = {m: sorted(hits[m]) for m in mods if m in hits}
    trace.set_events(4, 0)
    output.write_bytes(
        zlib.compress(json.dumps(result, separators=(",", ":")).encode(), 9)
    )
    print(len(result), len(records), flush=True)


if __name__ == "__main__":
    build(*map(Path, sys.argv[1:]))
