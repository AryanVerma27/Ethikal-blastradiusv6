"""Compare calls on a clean parsing boundary with the patched implementation."""

from functools import lru_cache
from pathlib import Path
import os
import enum
import hashlib
import importlib
import json
from stages import encode_expr


def pack(value):
    if isinstance(value, type):
        return {"class": [value.__module__, value.__qualname__]}
    if isinstance(value, enum.Enum):
        return {"enum": [type(value).__module__, type(value).__qualname__, value.name]}
    if isinstance(value, (list, tuple)):
        return [pack(x) for x in value]
    if isinstance(value, dict):
        return {k: pack(v) for k, v in value.items()}
    if type(value) in (str, int, bool, type(None), float):
        return value
    raise TypeError(type(value).__name__)


def unpack(value):
    if isinstance(value, list):
        return [unpack(x) for x in value]
    if isinstance(value, dict):
        for key in ("class", "enum"):
            if key in value and len(value) == 1:
                module, name, *member = value[key]
                cls = importlib.import_module(module)
                for part in name.split("."):
                    cls = getattr(cls, part)
                return cls[member[0]] if member else cls
        return {k: unpack(v) for k, v in value.items()}
    return value


def describe(dialect, method, args, kwargs):
    return [
        pack(type(dialect)),
        method,
        pack(args),
        pack(kwargs),
        pack(dialect.settings),
        list(dialect.version),
        dialect.normalization_strategy.name,
    ]


def digest(result, positions):
    values = encode_expr(result)
    if not positions:
        # TPC executor assertions compare row values and columns. Preserve all
        # semantic metadata while dropping the four source-coordinate fields.
        pending = [values]
        while pending:
            val = pending.pop()
            if isinstance(val, list):
                if len(val) == 7 and val[0] == "node" and isinstance(val[-1], dict):
                    val[-1] = {
                        k: v
                        for k, v in val[-1].items()
                        if k not in ("line", "col", "start", "end")
                    } or None
                pending.extend(val)
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


_cache = {}


@lru_cache(maxsize=1)
def fixed_digests():
    path = Path(__file__).with_name("parser-digests.json")
    if os.environ.get("PYTHONHASHSEED") == "0" and path.is_file():
        return json.loads(path.read_bytes())
    return {}


@lru_cache(maxsize=1)
def query_rows():
    return json.loads(Path(__file__).with_name("parse-rows.json").read_bytes())


def replay(entries, positions, test_name):
    affected = set()
    rows = query_rows().get(test_name, {})
    fixed = fixed_digests()
    for spec, expected in entries:
        encoded = json.dumps(spec, separators=(",", ":"))
        key = (encoded, positions)
        if fixed:
            digest_key = hashlib.sha256(
                (
                    "[" + encoded + "," + ("true" if positions else "false") + "]"
                ).encode()
            ).hexdigest()
            expected = fixed.get(digest_key, expected)
        if key not in _cache:
            cls, method, args, kwargs, settings, version, strategy = spec
            try:
                d = unpack(cls)(
                    version=".".join(map(str, version)),
                    normalization_strategy=strategy,
                    **unpack(settings),
                )
                result = getattr(d, method)(*unpack(args), **unpack(kwargs))
                _cache[key] = digest(result, positions)
            except Exception:
                _cache[key] = None
        if _cache[key] != expected:
            hit = rows.get(hashlib.sha256(encoded.encode()).hexdigest())
            if not hit or -1 in hit:
                return None
            affected.update(hit)
            if len(affected) > 12:
                return None
    return sorted(affected)
