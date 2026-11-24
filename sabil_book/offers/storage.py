"""Result objects never share the public media storage."""

from functools import cached_property
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
    """Private S3-compatible bucket (SeaweedFS in Compose).

    Objects are private by default; no ACL is sent, so the access key never
    needs permission to change ACLs (which would let it make objects public).
    """

    def __init__(self, **kwargs):
        options = {
            "bucket_name": settings.ATTACHMENT_S3_BUCKET,
            "endpoint_url": settings.ATTACHMENT_S3_ENDPOINT_URL,
            "region_name": settings.ATTACHMENT_S3_REGION,
            "access_key": settings.ATTACHMENT_S3_ACCESS_KEY,
            "secret_key": settings.ATTACHMENT_S3_SECRET_KEY,
            "addressing_style": "path",
            "signature_version": "s3v4",
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": settings.ATTACHMENT_DOWNLOAD_TTL_SECONDS,
            "custom_domain": None,
            "file_overwrite": False,
            "object_parameters": {"CacheControl": "private, no-store"},
        }
        options.update(kwargs)
        super().__init__(**options)

    @cached_property
    def _public_signer(self):
        # Presigning is local computation; this client never opens a connection.
        return PrivateAttachmentStorage(
            endpoint_url=settings.ATTACHMENT_S3_PUBLIC_ENDPOINT_URL,
        )

    def url(self, name, parameters=None, expire=None, http_method=None):
        # Workers reach storage on the internal network, but browsers need the
        # public host. SigV4 signs the host, so download URLs are signed for it.
        public = settings.ATTACHMENT_S3_PUBLIC_ENDPOINT_URL
        if not public or public == self.endpoint_url:
            return super().url(name, parameters, expire, http_method)
        return self._public_signer.url(name, parameters, expire, http_method)
