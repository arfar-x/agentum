"""kb-sources.yaml as a file the console may rewrite.

Written **in place**, not by writing a neighbour and renaming it over the
original: compose bind-mounts this one file, and a rename would replace the
inode the container sees with one the host does not, so the next read would
show the old content. The cost is that the write is not atomic, so it is
verified instead: the file is read back, and if it is not exactly what was meant
to be written -- a full disk, a mount that failed half-way -- the previous bytes
are put back before the failure is reported. A crash in the middle of the write
itself is the one window left; every previous version is in the revision
history for that (docs/OPERATIONS.md says how to restore one).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from kb.application.ports.source_files import FileChanged, FileNotWritable, FileSnapshot


def _etag(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


class FileSourcesFile:
    """Implements `kb.application.ports.source_files.SourcesFile`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def snapshot(self) -> FileSnapshot:
        data = self._path.read_bytes() if self._path.exists() else b""
        try:
            return FileSnapshot(text=data.decode("utf-8"), etag=_etag(data))
        except UnicodeDecodeError as exc:
            raise FileNotWritable(f"{self._path} is not valid UTF-8, so it will not be edited from here") from exc

    def writable(self) -> bool:
        if self._path.exists():
            return os.access(self._path, os.W_OK)
        return os.access(self._path.parent, os.W_OK)

    def write(self, text: str, *, expected_etag: str) -> FileSnapshot:
        if not self.writable():
            raise FileNotWritable(
                f"{self._path} cannot be written by the console (is it mounted read-only, "
                "or owned by a user the console does not run as? see KB_ADMIN_USER)"
            )
        current = self._path.read_bytes() if self._path.exists() else b""
        if _etag(current) != expected_etag:
            raise FileChanged("the file changed while the edit was being made")
        payload = text.encode("utf-8")
        existed = self._path.exists()
        try:
            self._overwrite(payload, existed)
            if self._path.read_bytes() != payload:
                raise OSError("what was read back is not what was written")
        except PermissionError as exc:
            raise FileNotWritable(str(exc)) from exc
        except OSError as exc:
            restored = self._restore(current, existed)
            raise FileNotWritable(
                f"{self._path} could not be written ({exc}); "
                + ("the previous content was put back" if restored else "the previous content could NOT be put back -- restore it from History")
            ) from exc
        return FileSnapshot(text=text, etag=_etag(payload))

    def _overwrite(self, payload: bytes, existed: bool) -> None:
        with open(self._path, "r+b" if existed else "wb") as handle:
            handle.seek(0)
            handle.write(payload)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())

    def _restore(self, previous: bytes, existed: bool) -> bool:
        try:
            if existed:
                self._overwrite(previous, True)
            else:
                self._path.unlink(missing_ok=True)
            return True
        except OSError:
            return False
