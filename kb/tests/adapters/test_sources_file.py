"""kb-sources.yaml as a file the console rewrites in place."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kb.adapters.outbound.sources_file import FileSourcesFile
from kb.application.ports.source_files import FileChanged, FileNotWritable


def test_a_write_keeps_the_same_file_so_a_bind_mount_still_sees_it(tmp_path):
    # Covers: FR-CFG-10
    # Writing a neighbour and renaming it over the original would give a
    # single-file bind mount a stale inode.
    path = tmp_path / "kb-sources.yaml"
    path.write_text("approved: true\n", encoding="utf-8")
    inode = os.stat(path).st_ino
    file = FileSourcesFile(path)

    written = file.write("approved: false\n", expected_etag=file.snapshot().etag)

    assert os.stat(path).st_ino == inode
    assert path.read_text(encoding="utf-8") == "approved: false\n"
    assert written.etag == file.snapshot().etag


def test_a_shorter_text_does_not_leave_the_old_tail_behind(tmp_path):
    # Covers: FR-CFG-10
    path = tmp_path / "kb-sources.yaml"
    path.write_text("x" * 500, encoding="utf-8")
    file = FileSourcesFile(path)
    file.write("short\n", expected_etag=file.snapshot().etag)
    assert path.read_text(encoding="utf-8") == "short\n"


def test_the_fingerprint_follows_the_exact_bytes(tmp_path):
    # Covers: FR-CFG-10
    path = tmp_path / "kb-sources.yaml"
    path.write_text("a\n", encoding="utf-8")
    first = FileSourcesFile(path).snapshot().etag
    path.write_text("a\n", encoding="utf-8")
    assert FileSourcesFile(path).snapshot().etag == first
    path.write_text("a \n", encoding="utf-8")
    assert FileSourcesFile(path).snapshot().etag != first


def test_a_write_against_a_stale_fingerprint_is_refused_and_changes_nothing(tmp_path):
    # Covers: FR-CFG-10
    path = tmp_path / "kb-sources.yaml"
    path.write_text("one\n", encoding="utf-8")
    file = FileSourcesFile(path)
    etag = file.snapshot().etag
    path.write_text("two\n", encoding="utf-8")
    with pytest.raises(FileChanged):
        file.write("three\n", expected_etag=etag)
    assert path.read_text(encoding="utf-8") == "two\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anything")
def test_a_file_the_console_may_not_write_says_so(tmp_path):
    # Covers: FR-CFG-10
    path = tmp_path / "kb-sources.yaml"
    path.write_text("one\n", encoding="utf-8")
    path.chmod(0o444)
    file = FileSourcesFile(path)
    assert file.writable() is False
    with pytest.raises(FileNotWritable, match="KB_ADMIN_USER"):
        file.write("two\n", expected_etag=file.snapshot().etag)


def _file(tmp_path, text="a: 1\n"):
    path = tmp_path / "kb-sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path, FileSourcesFile(path)


def test_a_write_that_fails_half_way_puts_the_old_file_back(tmp_path, monkeypatch):
    # Covers: FR-CFG-13
    import errno

    path, sources = _file(tmp_path, "original: true\n" * 20)
    etag = sources.snapshot().etag
    real_fsync = os.fsync
    calls = []

    def disk_full(fd):
        calls.append(fd)
        if len(calls) == 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(FileNotWritable, match="previous content was put back"):
        sources.write("replacement: true\n", expected_etag=etag)
    assert path.read_text(encoding="utf-8") == "original: true\n" * 20


def test_a_write_that_does_not_read_back_as_written_is_undone(tmp_path, monkeypatch):
    # Covers: FR-CFG-13
    path, sources = _file(tmp_path, "original: true\n")
    etag = sources.snapshot().etag
    real = Path.read_bytes
    seen = {"after_write": False}

    def lying(self):
        data = real(self)
        if self == path and seen["after_write"]:
            seen["after_write"] = False
            return data[:-3]  # what a failing mount might hand back
        return data

    monkeypatch.setattr(Path, "read_bytes", lying)
    real_overwrite = FileSourcesFile._overwrite

    def overwrite(self, payload, existed):
        real_overwrite(self, payload, existed)
        seen["after_write"] = True

    monkeypatch.setattr(FileSourcesFile, "_overwrite", overwrite)
    with pytest.raises(FileNotWritable, match="not what was written"):
        sources.write("replacement: true\n", expected_etag=etag)
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == "original: true\n"


def test_a_failed_first_write_leaves_no_file_behind(tmp_path, monkeypatch):
    # Covers: FR-CFG-13
    path = tmp_path / "new.yaml"
    sources = FileSourcesFile(path)
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(5, "I/O error")))
    with pytest.raises(FileNotWritable):
        sources.write("a: 1\n", expected_etag=sources.snapshot().etag)
    assert not path.exists()


def test_a_successful_write_reports_what_is_on_disk(tmp_path):
    # Covers: FR-CFG-13
    path, sources = _file(tmp_path)
    snapshot = sources.write("b: 2\n", expected_etag=sources.snapshot().etag)
    assert snapshot.text == "b: 2\n" and path.read_text(encoding="utf-8") == "b: 2\n"
