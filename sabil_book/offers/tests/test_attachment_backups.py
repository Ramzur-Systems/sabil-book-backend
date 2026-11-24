import io
import os
import tarfile
import time
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from sabil_book.offers.management.commands import backup_attachments
from sabil_book.offers.models import Attachment
from sabil_book.offers.models import Order
from sabil_book.offers.order_services import upload_result
from sabil_book.offers.tests.factories import OrderFactory


@pytest.fixture
def stored(db):
    order = OrderFactory(status=Order.OrderStatus.FUNDED, funded_at=timezone.now())
    upload_result(
        order.pk,
        order.offer.provider.user,
        SimpleUploadedFile("result.txt", b"result bytes"),
    )
    return order.attachments.get()


def _archives(path):
    return sorted(path.glob("attachments_*.tar.gz"))


def test_backup_archives_every_stored_object(stored, tmp_path):
    call_command("backup_attachments", output_dir=str(tmp_path), stdout=io.StringIO())
    (archive,) = _archives(tmp_path)
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    with tarfile.open(archive) as backup:
        member = backup.getmember(stored.file.name)
        assert backup.extractfile(member).read() == b"result bytes"
    assert not list(tmp_path.glob("*.partial"))


def test_backup_prunes_archives_older_than_retention(stored, tmp_path):
    old = tmp_path / "attachments_2000_01_01T00_00_00.tar.gz"
    old.write_bytes(b"")
    stale = time.time() - 31 * 86400
    os.utime(old, (stale, stale))
    unrelated = tmp_path / "keep-me.txt"
    unrelated.write_text("x")
    call_command(
        "backup_attachments", output_dir=str(tmp_path), keep_days=30,
        stdout=io.StringIO(),
    )
    assert not old.exists()
    assert unrelated.exists()
    assert len(_archives(tmp_path)) == 1


def test_backup_requires_existing_directory(db, tmp_path):
    with pytest.raises(CommandError):
        call_command("backup_attachments", output_dir=str(tmp_path / "missing"))


def test_restore_brings_back_only_referenced_objects(stored, tmp_path):
    storage, key = stored.file.storage, stored.file.name
    call_command("backup_attachments", output_dir=str(tmp_path), stdout=io.StringIO())
    (archive,) = _archives(tmp_path)
    storage.delete(key)
    purged_key = f"results/{stored.order_id}/purged.txt"
    with tarfile.open(archive, "r:gz") as src:
        members = [(m, src.extractfile(m).read()) for m in src]
    rebuilt = tmp_path / "with_purged.tar.gz"
    with tarfile.open(rebuilt, "w:gz") as dst:
        for member, data in [*members, (tarfile.TarInfo(purged_key), b"gone")]:
            member.size = len(data)
            dst.addfile(member, io.BytesIO(data))
    out = io.StringIO()
    call_command("restore_attachments", str(rebuilt), stdout=out)
    assert storage.exists(key)
    with storage.open(key) as restored:
        assert restored.read() == b"result bytes"
    assert not storage.exists(purged_key)  # No row: stays deleted.
    assert "Restored 1 objects, skipped 1" in out.getvalue()
    call_command("restore_attachments", str(rebuilt), stdout=out)  # Idempotent.
    assert Attachment.objects.count() == 1


@pytest.mark.parametrize("name", ["../etc/passwd", "/abs/path", "other/x.txt"])
def test_restore_rejects_unsafe_members(db, tmp_path, name):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as dst:
        info = tarfile.TarInfo(name)
        info.size = 1
        dst.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(CommandError):
        call_command("restore_attachments", str(archive))


def test_backup_skips_objects_deleted_while_it_runs(stored, tmp_path):
    storage = stored.file.storage
    gone = storage.save(f"results/{stored.order_id}/gone.txt", io.BytesIO(b"x"))
    real_read = backup_attachments._read_object  # noqa: SLF001

    def read_after_delete(storage_, key):
        if key == gone:
            storage_.delete(key)  # e.g. a purge committed mid-backup
        return real_read(storage_, key)

    err = io.StringIO()
    with patch.object(backup_attachments, "_read_object", read_after_delete):
        call_command(
            "backup_attachments", output_dir=str(tmp_path), stdout=io.StringIO(),
            stderr=err,
        )
    (archive,) = _archives(tmp_path)
    with tarfile.open(archive) as backup:
        assert backup.getnames() == [stored.file.name]
    assert gone in err.getvalue()


def test_archive_is_private_while_being_written(stored, tmp_path):
    real_read = backup_attachments._read_object  # noqa: SLF001
    modes = []

    def read_and_check(storage_, key):
        (partial,) = tmp_path.glob("*.partial")
        modes.append(partial.stat().st_mode & 0o777)
        return real_read(storage_, key)

    old_umask = os.umask(0o002)  # A permissive umask must not leak results.
    try:
        with patch.object(backup_attachments, "_read_object", read_and_check):
            call_command(
                "backup_attachments", output_dir=str(tmp_path), stdout=io.StringIO(),
            )
    finally:
        os.umask(old_umask)
    assert modes == [0o600]


def test_backup_keeps_seven_days_by_default(stored, tmp_path):
    week_old = tmp_path / "attachments_2000_01_01T00_00_00.tar.gz"
    week_old.write_bytes(b"")
    stale = time.time() - 8 * 86400
    os.utime(week_old, (stale, stale))
    call_command("backup_attachments", output_dir=str(tmp_path), stdout=io.StringIO())
    assert not week_old.exists()
