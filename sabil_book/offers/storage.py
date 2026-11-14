"""Result objects never share the public media storage."""

from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import storages
from storages.backends.s3 import S3Storage


def attachment_storage():
    return storages["attachments"]


def attachment_upload_to(instance, filename):
    return f"results/{instance.order_id}/{uuid4().hex}{Path(filename).suffix.lower()}"


class PrivateAttachmentStorage(S3Storage):
    def __init__(self, **kwargs):
        super().__init__(
            bucket_name=settings.ATTACHMENT_S3_BUCKET,
            endpoint_url=settings.ATTACHMENT_S3_ENDPOINT_URL,
            region_name=settings.ATTACHMENT_S3_REGION,
            access_key=settings.ATTACHMENT_S3_ACCESS_KEY,
            secret_key=settings.ATTACHMENT_S3_SECRET_KEY,
            addressing_style="path",
            signature_version="s3v4",
            default_acl="private",
            querystring_auth=True,
            querystring_expire=settings.ATTACHMENT_DOWNLOAD_TTL_SECONDS,
            custom_domain=None,
            file_overwrite=False,
            object_parameters={"CacheControl": "private, no-store"},
            **kwargs,
        )
