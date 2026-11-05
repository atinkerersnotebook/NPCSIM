import os
import threading

import pytest

import npc_sim.storage as storage
from npc_sim.storage import read_json, write_json, write_text


def test_markdown_writes_use_the_same_atomic_storage_and_preserve_empty_documents(tmp_path):
    path = tmp_path / "npc" / "MEMORY.md"
    write_text(path, "# Memory\n\nAuthored background.\n")
    assert path.read_text(encoding="utf-8") == "# Memory\n\nAuthored background.\n"
    write_text(path, "")
    assert path.read_text(encoding="utf-8") == ""
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows readers can temporarily block atomic replacement")
def test_atomic_write_survives_brief_open_reader(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"revision": 1})
    reader = path.open(encoding="utf-8")
    release = threading.Timer(0.08, reader.close)
    release.start()
    try:
        write_json(path, {"revision": 2})
    finally:
        release.join()
        reader.close()
    assert read_json(path) == {"revision": 2}


def test_persistent_replace_failure_raises_and_preserves_previous_json(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    write_json(path, {"revision": 1})
    attempts = []

    def blocked(source, destination):
        attempts.append(destination)
        error = PermissionError("Windows file handle remains open")
        error.winerror = 5
        raise error

    monkeypatch.setattr(storage.os, "replace", blocked)
    monkeypatch.setattr(storage.time, "sleep", lambda delay: None)
    with pytest.raises(PermissionError):
        write_json(path, {"revision": 2})
    assert len(attempts) == 6
    assert read_json(path) == {"revision": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_non_windows_permission_failure_is_not_retried(tmp_path, monkeypatch):
    attempts = []

    def denied(source, destination):
        attempts.append(destination)
        raise PermissionError("Permission denied")

    monkeypatch.setattr(storage.os, "replace", denied)
    with pytest.raises(PermissionError):
        write_json(tmp_path / "state.json", {"revision": 1})
    assert len(attempts) == 1
    assert not list(tmp_path.glob("*.tmp"))
