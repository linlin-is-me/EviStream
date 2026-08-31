"""Filesystem-backed artifact storage with traversal-safe URIs."""

import shutil
from pathlib import Path
from typing import BinaryIO


class LocalArtifactStore:
    scheme = "artifact://"

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, source: Path, key: str) -> str:
        target = self.path_for_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return self.uri_for_key(key)

    def put_stream(self, source: BinaryIO, key: str) -> str:
        target = self.path_for_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as output:
            shutil.copyfileobj(source, output)
        return self.uri_for_key(key)

    def write_text(self, content: str, key: str) -> str:
        target = self.path_for_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self.uri_for_key(key)

    def resolve(self, uri: str) -> Path:
        if not uri.startswith(self.scheme):
            raise ValueError("unsupported artifact URI")
        return self.path_for_key(uri.removeprefix(self.scheme))

    def uri_for_key(self, key: str) -> str:
        relative = self._safe_relative(key)
        return f"{self.scheme}{relative.as_posix()}"

    def path_for_key(self, key: str) -> Path:
        relative = self._safe_relative(key)
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("artifact key escapes storage root")
        return resolved

    @staticmethod
    def _safe_relative(key: str) -> Path:
        candidate = Path(key.replace("\\", "/"))
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise ValueError("invalid artifact key")
        return candidate
