"""Archive every stored result object into a dated .tar.gz and prune old ones.

Objects are written once and never modified (unique keys, no overwrite), so
reading them through the S3 API gives a consistent backup without stopping
storage. Archives older than the retention period are deleted, so results the
purge task removed do not outlive it in backups.
"""

import tarfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from sabil_book.offers.storage import attachment_storage
from sabil_book.offers.tasks import _stored_keys

ARCHIVE_PREFIX = "attachments_"
ARCHIVE_SUFFIX = ".tar.gz"


class Command(BaseCommand):
    help = "Back up stored result attachments and prune expired backups."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default="/backups/attachments")
        parser.add_argument(
            "--keep-days",
            type=int,
            default=settings.ATTACHMENT_RETENTION_DAYS,
            help="Delete archives older than this (default: retention period).",
        )

    def handle(self, *args, output_dir, keep_days, **options):
        out = Path(output_dir)
        if not out.is_dir():
            msg = f"Output directory {out} does not exist."
            raise CommandError(msg)
        storage = attachment_storage()
        stamp = timezone.now().strftime("%Y_%m_%dT%H_%M_%S")
        final = out / f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}"
        partial = final.with_name(final.name + ".partial")
        try:
            keys = list(_stored_keys(storage, "results"))
        except FileNotFoundError:
            keys = []
        try:
            with tarfile.open(partial, "w:gz") as archive:
                for key in keys:
                    with storage.open(key, "rb") as stream:
                        info = tarfile.TarInfo(key)
                        info.size = storage.size(key)
                        info.mtime = int(storage.get_modified_time(key).timestamp())
                        info.mode = 0o600
                        archive.addfile(info, stream)
            partial.chmod(0o600)
            partial.rename(final)  # Only complete archives ever get the final name.
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        self.stdout.write(f"Backed up {len(keys)} objects to {final}.")
        cutoff = timezone.now() - timedelta(days=keep_days)
        for old in sorted(out.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}")):
            if old != final and old.stat().st_mtime < cutoff.timestamp():
                old.unlink()
                self.stdout.write(f"Deleted expired backup {old.name}.")
