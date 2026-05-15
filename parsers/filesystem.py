"""Filesystem-backed data source.

Reads files out of an extracted SRO asset tree using the same
backslash-separated paths the Pk2 archives use internally. Path
lookup is case-insensitive at the leaf (the original archives are
case-insensitive; macOS APFS is case-preserving so the on-disk name
may differ).
"""

from __future__ import annotations

from pathlib import Path


class FilesystemDataSource:
    """Read files out of a folder using Pk2-style backslash paths."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"data root is not a directory: {self.root}")

    def _resolve(self, path: str) -> Path:
        rel = path.replace("\\", "/").lstrip("/")
        return self.root / rel

    def file_exists(self, path: str) -> bool:
        target = self._resolve(path)
        if target.is_file():
            return True
        return self._case_insensitive_lookup(target) is not None

    def _case_insensitive_lookup(self, target: Path) -> Path | None:
        if not target.parent.is_dir():
            return None
        lc = target.name.lower()
        for entry in target.parent.iterdir():
            if entry.name.lower() == lc:
                return entry
        return None

    def read_file(self, path: str) -> bytes:
        target = self._resolve(path)
        if not target.is_file():
            target = self._case_insensitive_lookup(target) or target
        if not target.is_file():
            raise FileNotFoundError(f"file not in data source: {path}")
        return target.read_bytes()

    def read_text(self, path: str) -> str:
        """Read a file with auto-detected encoding (UTF-16 LE BOM, UTF-8 BOM, latin-1)."""
        data = self.read_file(path)
        if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xFE:
            return data[2:].decode("utf-16-le", errors="replace")
        if len(data) >= 3 and data[0] == 0xEF and data[1] == 0xBB and data[2] == 0xBF:
            return data[3:].decode("utf-8", errors="replace")
        # Object.ifo on this user's tree is ISO-8859 (per `file` probe). UTF-8 may fail
        # on accented building names; latin-1 is a lossless 1:1 fallback.
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")
