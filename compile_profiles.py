"""Pack clean coverage profiles by source path for on-demand runtime reads."""

import ast
import json
import marshal
import sys
import zlib
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def compile_profiles(directory, source):
    files = defaultdict(lambda: defaultdict(dict))

    def raw(file):
        return json.loads((directory / file).read_bytes())

    def group(key):
        return key.split(":", 1)[0]

    def load_base(name, field, shift=0):
        d = raw(name + ".json")
        masks = [int(s, 16) << shift for s in d["masks"]]
        for path, entries in d["lines"].items():
            bucket = files[group(path)][field]
            if field == "lines":
                ls = bucket.setdefault(path, {})
                for n, index in entries.items():
                    n = int(n)
                    ls[n] = ls.get(n, 0) | masks[index]
            else:
                bucket[path] = {
                    (k if field in ("values", "guards") else int(k)): masks[i]
                    for k, i in entries.items()
                }
        return d, masks

    base, masks = load_base("coverage", "lines")
    names = base["tests"]
    fixtures = {p: masks[i] for p, i in base["fixtures"].items()}
    cases, _ = load_base("cases", "lines", len(names))
    stage_offset = len(names) + len(cases["tests"])
    stages, _ = load_base("stages", "lines", stage_offset)
    offset = stage_offset + len(stages["tests"])
    for name in ("values", "guards", "branches"):
        other, _ = load_base(name, name)
        if other["tests"] != names:
            raise ValueError(name)
    groups = defaultdict(dict)
    for kind in ("rows", "calls", "boundary", "engine"):
        data = json.loads(zlib.decompress((directory / (kind + ".z")).read_bytes()))
        for name, record in data.items():
            masks = [int(s, 16) << offset for s in record["masks"]]
            for path, entries in record["lines"].items():
                ls = files[group(path)]["lines"].setdefault(path, {})
                for n, index in entries.items():
                    n = int(n)
                    ls[n] = ls.get(n, 0) | masks[index]
            count = len(record["rows"])
            groups[name][kind] = (
                offset,
                count,
                record["rows"],
                record.get("positions", True),
            )
            offset += count + 1
    for p in source.rglob("*.py"):
        path = p.relative_to(source).as_posix()
        if path not in files:
            continue
        tree = ast.parse(p.read_bytes())
        files[path]["scopes"] = {
            n.lineno: (n.body[0].lineno, n.end_lineno)
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.body
        }
    meta = dict(
        names=names,
        fixtures=fixtures,
        types=base["types"],
        case_records=cases["tests"],
        cpu=cases["cpu"],
        uncertain=cases["uncertain"],
        stage_records=stages["tests"],
        inputs=stages["plan_inputs"],
        groups=dict(groups),
        bits=offset,
    )
    with ZipFile(directory / "profiles.zip", "w", ZIP_DEFLATED, compresslevel=7) as z:
        z.writestr("meta", marshal.dumps(meta, 4))
        for p, bucket in files.items():
            # Intern repeated bitsets before serializing, including long sparse masks.
            shared = {}
            for field, section in bucket.items():
                if field == "scopes":
                    continue
                for entry in section.values():
                    for k, b in entry.items():
                        entry[k] = shared.setdefault(b, b)
            z.writestr("file/" + p, marshal.dumps(dict(bucket), 4))


if __name__ == "__main__":
    compile_profiles(Path(sys.argv[1]), Path(sys.argv[2]))
