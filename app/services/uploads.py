"""Image uploads (assistant avatar, app logo).

Files are identified by their first bytes, not by the name or the browser's
declared type, saved under a random name, and served from /media/<kind>/<name>.
SVG is not accepted: it can carry script.
"""

import logging
import re
import uuid
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 2 * 1024 * 1024
PUBLIC_KINDS = {"avatars", "logos"}
FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpg|gif|webp)$")


class UploadError(ValueError):
    pass


def sniff_image_type(head: bytes):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def upload_dir(kind):
    if kind not in PUBLIC_KINDS:
        raise ValueError(f"Unknown upload kind: {kind}")
    path = Path(current_app.config["UPLOAD_FOLDER"]) / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image(file_storage, kind):
    """Validate and store an uploaded image. Returns its public URL path."""
    if file_storage is None or not file_storage.filename:
        raise UploadError("Choose an image to upload.")
    data = file_storage.stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise UploadError("The image must be 2 MB or smaller.")
    if not data:
        raise UploadError("The file is empty.")
    extension = sniff_image_type(data[:16])
    if extension is None:
        raise UploadError("Use a PNG, JPG, GIF or WebP image.")
    name = f"{uuid.uuid4().hex}.{extension}"
    (upload_dir(kind) / name).write_bytes(data)
    return f"/media/{kind}/{name}"


def delete_upload(url):
    """Remove a file previously returned by save_image. Ignores anything else."""
    match = re.match(r"^/media/([a-z]+)/([^/]+)$", url or "")
    if not match or match.group(1) not in PUBLIC_KINDS or not FILENAME_RE.match(match.group(2)):
        return
    path = upload_dir(match.group(1)) / match.group(2)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # e.g. Windows refuses while the file is being downloaded. The new image is
        # already saved, so a leftover old file is harmless: log it and move on.
        log.warning("Could not delete old upload %s", path, exc_info=True)
