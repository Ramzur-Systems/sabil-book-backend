from django.db import migrations
from django.db import models

LEGACY_NOTE = "Legacy attachment without a stored file; never scanned."


def mark_legacy_attachments(apps, schema_editor):
    # Rows from before private storage have no file to scan. Leaving them
    # PENDING would block uploads, reviews and auto-completion of their orders.
    # They stay undownloadable because the download view requires a file.
    Attachment = apps.get_model("offers", "Attachment")
    Attachment.objects.using(schema_editor.connection.alias).filter(
        av_scan_status="pending",
        file="",
    ).update(av_scan_status="clean", av_scan_error=LEGACY_NOTE)


def unmark_legacy_attachments(apps, schema_editor):
    Attachment = apps.get_model("offers", "Attachment")
    Attachment.objects.using(schema_editor.connection.alias).filter(
        av_scan_status="clean",
        av_scan_error=LEGACY_NOTE,
    ).update(av_scan_status="pending", av_scan_error="")


class Migration(migrations.Migration):
    dependencies = [
        ("offers", "0011_attachment_av_scan_error_attachment_av_scan_status_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="attachment",
            name="av_scan_queued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_legacy_attachments, unmark_legacy_attachments),
    ]
