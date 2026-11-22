"""Capture parser comparisons from the pinned runtime with a fixed hash seed."""

import os
import sys
import json
from pathlib import Path
from profile_store import Store
from boundary import digest, unpack


def main(source, output):
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise ValueError("run with PYTHONHASHSEED=0")
    sys.path.insert(0, str(source.resolve()))
    data = {}
    for groups in Store().meta["groups"].values():
        if "boundary" not in groups:
            continue
        _, _, entries, positions = groups["boundary"]
        for spec, original in entries:
            key = json.dumps([spec, positions], separators=(",", ":"))
            if key in data:
                continue
            cls, method, args, kwargs, settings, version, strategy = spec
            dialect = unpack(cls)(
                version=".".join(map(str, version)),
                normalization_strategy=strategy,
                **unpack(settings),
            )
            value = getattr(dialect, method)(*unpack(args), **unpack(kwargs))
            data[key] = digest(value, positions)
    import hashlib

    output.write_text(
        json.dumps(
            {hashlib.sha256(k.encode()).hexdigest(): v for k, v in data.items()},
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
