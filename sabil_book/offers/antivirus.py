"""Bounded ClamAV INSTREAM client; only an explicit OK is trusted."""

import codecs
import socket
import struct
import warnings
from hashlib import sha256
from pathlib import Path

from django.conf import settings
from PIL import Image
from pypdf import PdfReader
from rest_framework.exceptions import ValidationError

MAX_RESPONSE_BYTES = 4096


class ScanError(Exception):
    pass


def validate_result_file(upload):
    if not upload.size or upload.size > settings.ORDER_RESULT_MAX_BYTES:
        msg = "The result file is empty or too large."
        raise ValidationError(msg)
    extension = Path(upload.name).suffix.lower().lstrip(".")
    if extension not in settings.ORDER_RESULT_ALLOWED_EXTENSIONS:
        msg = "This result file format is not allowed."
        raise ValidationError(msg)
    header = upload.read(1024)
    upload.seek(0)
    signatures = {
        "pdf": (b"%PDF-",),
        "png": (b"\x89PNG\r\n\x1a\n",),
        "jpg": (b"\xff\xd8\xff",),
        "jpeg": (b"\xff\xd8\xff",),
    }
    if extension in signatures and not header.startswith(signatures[extension]):
        msg = "The file content does not match its format."
        raise ValidationError(msg)
    if extension == "txt" and (b"\x00" in header or not header.isascii()):
        try:
            codecs.getincrementaldecoder("utf-8")().decode(
                header,
                final=upload.size <= len(header),
            )
        except UnicodeDecodeError as exc:
            msg = "Text results must use UTF-8."
            raise ValidationError(msg) from exc
        if b"\x00" in header:
            msg = "The file is not a text document."
            raise ValidationError(msg)
    return upload


def scan_stream(stream, expected_hash):
    digest = sha256()
    size = 0
    with socket.create_connection(
        (settings.CLAMAV_HOST, settings.CLAMAV_PORT),
        timeout=settings.CLAMAV_TIMEOUT_SECONDS,
    ) as connection:
        connection.sendall(b"zINSTREAM\0")
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > settings.ORDER_RESULT_MAX_BYTES:
                msg = "Stored result exceeds the size limit."
                raise ScanError(msg)
            digest.update(chunk)
            connection.sendall(struct.pack("!I", len(chunk)) + chunk)
        connection.sendall(struct.pack("!I", 0))
        response = bytearray()
        while len(response) < MAX_RESPONSE_BYTES:
            chunk = connection.recv(1024)
            if not chunk:
                break
            response.extend(chunk)
            if b"\0" in response:
                break
    if not size or digest.hexdigest() != expected_hash:
        msg = "Stored result is damaged (SHA-256 mismatch)."
        raise ScanError(msg)
    if not response.endswith(b"\0") or len(response) > MAX_RESPONSE_BYTES:
        msg = "ClamAV returned an incomplete or oversized response."
        raise ScanError(msg)
    reply = bytes(response[:-1])
    if reply == b"stream: OK":
        return "clean"
    if reply.startswith(b"stream: ") and reply.endswith(b" FOUND"):
        return "infected"
    msg = "ClamAV did not confirm a successful scan."
    raise ScanError(msg)


def validate_document(stream, filename):
    """Reject malformed and encrypted documents even when no malware was found."""
    extension = Path(filename).suffix.lower()
    stream.seek(0)
    try:
        if extension == ".txt":
            decoder = codecs.getincrementaldecoder("utf-8")()
            while chunk := stream.read(64 * 1024):
                if b"\0" in chunk:
                    msg = "Text results must not contain binary data."
                    raise ScanError(msg)  # noqa: TRY301
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
        elif extension in {".png", ".jpg", ".jpeg"}:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(stream) as image:
                    if image.format == "JPEG":
                        image.load()
                    else:
                        image.verify()
        elif extension == ".pdf":
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted or not reader.pages:
                msg = "Encrypted or empty PDFs cannot be published."
                raise ScanError(msg)  # noqa: TRY301
    except ScanError:
        raise
    except Exception as exc:
        msg = "The result document is damaged or unreadable."
        raise ScanError(msg) from exc
