"""Identify standalone validator calls from their actual arguments."""

import hashlib
import json

METHODS = ("validate_identity", "validate_all", "validate_transpile")


def key_for(method, line, args, kwargs):
    def special(obj):
        if isinstance(obj, type):
            return obj.__module__ + "." + obj.__qualname__
        raise TypeError

    try:
        data = json.dumps(
            (method, line, args, kwargs), default=special, ensure_ascii=True
        )
    except (ValueError, TypeError):
        return None
    return hashlib.blake2b(data.encode(), digest_size=12).hexdigest()
