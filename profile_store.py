import marshal
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile


class Store:
    def __init__(self):
        self.archive = ZipFile(Path(__file__).with_name("profiles.zip"))
        self.meta = marshal.loads(self.archive.read("meta"))

    @lru_cache(maxsize=24)
    def file(self, path):
        try:
            return marshal.loads(self.archive.read("file/" + path))
        except KeyError:
            return {}
