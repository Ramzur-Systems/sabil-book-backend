"""Restore result objects from a backup_attachments archive.

Only objects that an Attachment row still references are restored, so a
restore never brings back results the retention purge already deleted. Restore
the database first, then this. Existing objects are left untouched.
"""

import tarfile
from pathlib import PurePosixPath

from django.core.files import File
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from sabil_book.offers.models import Attachment
from sabil_book.offers.storage import attachment_storage


class Command(BaseCommand):
    help = "Restore referenced result attachments from a backup archive."

    def add_arguments(self, parser):
        parser.add_argument("archive")

    def handle(self, *args, archive, **options):
        storage = attachment_storage()
        referenced = set(
            Attachment.objects.exclude(file="").values_list("file", flat=True),
        )
        restored = skipped = 0
        try:
            backup = tarfile.open(archive, "r:gz")  # noqa: SIM115 - closed below
        except (OSError, tarfile.TarError) as exc:
            msg = f"Cannot read {archive}: {exc}"
            raise CommandError(msg) from exc
        with backup:
            for member in backup:
                key = member.name
                path = PurePosixPath(key)
                if (
                    not member.isfile()
                    or path.is_absolute()
                    or ".." in path.parts
                    or path.parts[:1] != ("results",)
                ):
                    msg = f"Unexpected archive member {key!r}."
                    raise CommandError(msg)
                if key not in referenced or storage.exists(key):
                    skipped += 1
                    continue
                # save() keeps the exact key because no object exists under it.
                saved = storage.save(key, File(backup.extractfile(member)))
                if saved != key:
                    storage.delete(saved)
                    msg = f"Storage renamed {key!r} to {saved!r}; aborting."
                    raise CommandError(msg)
                restored += 1
        self.stdout.write(f"Restored {restored} objects, skipped {skipped}.")
