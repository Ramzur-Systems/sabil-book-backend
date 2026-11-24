"""Archive every stored result object into a dated .tar.gz and prune old ones.

Objects are written once and never modified (unique keys, no overwrite), so
reading them through the S3 API gives a consistent backup without stopping
storage. Objects deleted while the backup runs (rejections, purges, sweeps)
are skipped.

Archives are deleted after --keep-days (ATTACHMENT_BACKUP_KEEP_DAYS, default
7), so a result the retention purge removes stays in backups for at most that
many days afterwards.
"""

import os
import shutil
import tarfile
from datetime import timedelta
from pathlib import Path
from tempfile import SpooledTemporaryFile

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from sabil_book.offers.storage import attachment_storage
from sabil_book.offers.tasks import _stored_keys

ARCHIVE_PREFIX = "attachments_"
ARCHIVE_SUFFIX = ".tar.gz"
SPOOL_MAX_BYTES = 32 * 1024 * 1024


def _is_missing(exc):
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        return code in {"404", "NoSuchKey", "NotFound"}
    return False


def _read_object(storage, key):
    """Copy one object to a local buffer; None if it no longer exists.

    Reading fully before writing the tar entry means an object that vanishes
    mid-read can never leave a truncated entry in the archive.
    """
    buffer = SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES)  # noqa: SIM115
    try:
        mtime = storage.get_modified_time(key)
        with storage.open(key, "rb") as stream:
            shutil.copyfileobj(stream, buffer)
    except (FileNotFoundError, ClientError) as exc:
        buffer.close()
        if _is_missing(exc):
            return None
        raise
    size = buffer.tell()
    buffer.seek(0)
    return buffer, size, mtime


class Command(BaseCommand):
    help = "Back up stored result attachments and prune expired backups."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default="/backups/attachments")
        parser.add_argument(
            "--keep-days",
            type=int,
            default=settings.ATTACHMENT_BACKUP_KEEP_DAYS,
            help="Delete archives older than this many days.",
        )

    def handle(self, *args, output_dir, keep_days, **options):
        out = Path(output_dir)
        if not out.is_dir():
            msg = f"Output directory {out} does not exist."
            raise CommandError(msg)
        if keep_days < 1:
            msg = "--keep-days must be at least 1."
            raise CommandError(msg)
        storage = attachment_storage()
        stamp = timezone.now().strftime("%Y_%m_%dT%H_%M_%S")
        final = out / f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}"
        partial = final.with_name(final.name + ".partial")
        try:
            keys = list(_stored_keys(storage, "results"))
        except FileNotFoundError:
            keys = []
        archived = skipped = 0
        # Private from the first byte: the archive holds customers' results.
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with (
                os.fdopen(fd, "wb") as raw,
                tarfile.open(
                    fileobj=raw,
                    mode="w:gz",
                ) as archive,
            ):
                for key in keys:
                    copied = _read_object(storage, key)
                    if copied is None:
                        skipped += 1
                        self.stderr.write(f"Skipped {key}: deleted during backup.")
                        continue
                    buffer, size, mtime = copied
                    with buffer:
                        info = tarfile.TarInfo(key)
                        info.size = size
                        info.mtime = int(mtime.timestamp())
                        info.mode = 0o600
                        archive.addfile(info, buffer)
                    archived += 1
            partial.rename(final)  # Only complete archives get the final name.
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        self.stdout.write(
            f"Backed up {archived} objects to {final} ({skipped} skipped).",
        )
        cutoff = (timezone.now() - timedelta(days=keep_days)).timestamp()
        for old in sorted(out.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}")):
            if old != final and old.stat().st_mtime < cutoff:
                old.unlink()
                self.stdout.write(f"Deleted expired backup {old.name}.")
