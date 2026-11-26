import sys
from pathlib import Path

import pytest

from gitkeeper.queue.errors import StorageUnavailable
from gitkeeper.worker.win_acl import (
    create_owner_only_file,
    verify_owner_only_file,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows DACL behavior is Windows-only."
)


def test_create_and_verify_an_owner_only_file(tmp_path) -> None:
    path = tmp_path / "private.sqlite3"

    create_owner_only_file(path)
    verify_owner_only_file(path)

    assert path.is_file()
    assert path.read_bytes() == b""


def test_existing_private_file_is_reused_without_truncation(tmp_path) -> None:
    path = tmp_path / "private.sqlite3"
    create_owner_only_file(path)
    path.write_text("data", encoding="utf-8")

    create_owner_only_file(path)

    assert path.read_text(encoding="utf-8") == "data"


def test_inherited_dacl_is_rejected(tmp_path) -> None:
    path = tmp_path / "plain.txt"
    path.touch()

    with pytest.raises(StorageUnavailable):
        verify_owner_only_file(path)


def test_missing_file_is_rejected(tmp_path) -> None:
    with pytest.raises(StorageUnavailable):
        verify_owner_only_file(tmp_path / "missing")


def test_relative_path_is_rejected() -> None:
    with pytest.raises(StorageUnavailable):
        create_owner_only_file(Path("relative.txt"))
