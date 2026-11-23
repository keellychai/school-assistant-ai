"""Knowledge documents: validate, store privately, and extract plain text.

Accepted: PDF, Word (.docx), plain text (.txt) and Markdown (.md), up to 10 MB.
Files are recognised by their content, stored under a random name in
instance/uploads/knowledge/ (NOT publicly served), and their text is extracted
so it can be searched and given to the AI as context.
"""

import io
import logging
import re
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from flask import current_app

log = logging.getLogger(__name__)

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_PDF_PAGES = 300
MAX_DOCX_XML_BYTES = 30 * 1024 * 1024   # guards against "zip bomb" files
STORED_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(pdf|docx|txt|md)$")
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentError(ValueError):
    pass


def documents_dir():
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "knowledge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect_type(data: bytes, filename: str):
    """Return pdf/docx/txt/md, or None. Text types also require the matching extension."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            return None
        return None
    extension = Path(filename or "").suffix.lower().lstrip(".")
    if extension in ("txt", "md") and b"\x00" not in data:
        try:
            data.decode("utf-8-sig")
            return extension
        except UnicodeDecodeError:
            return None
    return None


def _clean(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


def _pdf_text(data):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("This PDF is password-protected. Upload an unprotected copy.")
        pages = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            pages.append(page.extract_text() or "")
        return "\n\n".join(pages)
    except PdfReadError as error:
        raise DocumentError("This PDF couldn't be read. It may be damaged.") from error


def _docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > MAX_DOCX_XML_BYTES:
            raise DocumentError("This Word document is too large to read.")
        root = ElementTree.fromstring(archive.read(info))
    paragraphs = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{WORD_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{WORD_NS}tab":
                parts.append("\t")
            elif node.tag in (f"{WORD_NS}br", f"{WORD_NS}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def extract_text(data, kind):
    if kind == "pdf":
        return _clean(_pdf_text(data))
    if kind == "docx":
        try:
            return _clean(_docx_text(data))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
            raise DocumentError("This Word document couldn't be read. It may be damaged.") from error
    return _clean(data.decode("utf-8-sig"))


def save_document(file_storage):
    """Validate, store and extract. Returns dict(path, name, type, size, text)."""
    if file_storage is None or not file_storage.filename:
        raise DocumentError("Choose a file to upload.")
    data = file_storage.stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError("The file must be 10 MB or smaller.")
    if not data:
        raise DocumentError("The file is empty.")
    kind = detect_type(data, file_storage.filename)
    if kind is None:
        raise DocumentError("Use a PDF, Word (.docx), .txt or .md file.")
    text = extract_text(data, kind)

    stored = f"{uuid.uuid4().hex}.{kind}"
    (documents_dir() / stored).write_bytes(data)
    original = Path(file_storage.filename).name[:255] or f"document.{kind}"
    return {"path": f"knowledge/{stored}", "name": original, "type": kind, "size": len(data), "text": text}


def document_file(relative_path):
    """Absolute path for a stored document, or None if the reference is invalid/missing."""
    match = re.match(r"^knowledge/([^/]+)$", relative_path or "")
    if not match or not STORED_NAME_RE.match(match.group(1)):
        return None
    path = documents_dir() / match.group(1)
    return path if path.is_file() else None


def delete_document(relative_path):
    path = document_file(relative_path)
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        log.warning("Could not delete knowledge document %s", path, exc_info=True)
