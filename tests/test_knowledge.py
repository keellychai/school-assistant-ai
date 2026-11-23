"""Step 8: knowledge base (categories, entries, visibility, documents, match tester, chat integration)."""

import io
import zipfile

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import ActivityLog, KnowledgeCategory, KnowledgeEntry
from app.services.documents import documents_dir

from .conftest import ask, login
from .docs_util import make_docx, make_pdf

API = "/api/admin/knowledge"


def cat_id(slug="administration"):
    return db.session.scalar(select(KnowledgeCategory.id).where(KnowledgeCategory.slug == slug))


def create(client, **overrides):
    body = {"title": "Biaya Studi Wisata", "category_id": cat_id(), "external_ref": "sch012",
            "content_markdown": "Biaya studi wisata kelas XI-A adalah **Rp 350.000**.",
            "keywords": ["biaya studi wisata", "harga study tour"], "source_ref": "Surat edaran No. 12",
            "available_to_ai": True, "is_published": True, **overrides}
    response = client.post(API, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def upload(client, entry_id, data, name):
    return client.post(f"{API}/{entry_id}/document", data={"document": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


# --- Categories ------------------------------------------------------------------------

def test_category_crud(admin_client):
    created = admin_client.post(f"{API}/categories", json={"name": "Ekstrakurikuler"}).get_json()
    assert created["slug"] == "ekstrakurikuler" and created["entry_count"] == 0
    assert admin_client.put(f"{API}/categories/{created['id']}", json={"name": "Klub & Ekskul", "description": "Klub"}).status_code == 200
    assert admin_client.delete(f"{API}/categories/{created['id']}").status_code == 204


def test_category_rules(admin_client):
    assert admin_client.post(f"{API}/categories", json={"name": "jadwal"}).status_code == 422  # duplicate name
    response = admin_client.delete(f"{API}/categories/{cat_id('schedule')}")
    assert response.status_code == 409 and "still has 5 entries" in response.get_json()["error"]


# --- Entries ----------------------------------------------------------------------------

def test_create_entry(admin_client):
    data = create(admin_client)
    assert data["external_ref"] == "SCH012"  # normalised to upper case
    assert data["content_html"] == "<p>Biaya studi wisata kelas XI-A adalah <strong>Rp 350.000</strong>.</p>"
    assert data["used_by_chat"] is True and data["created_by"] is not None
    log = db.session.scalar(select(ActivityLog).where(ActivityLog.entity_type == "knowledge_entries"))
    assert log.action == "create"


@pytest.mark.parametrize("overrides,field", [
    ({"title": "x"}, "title"),
    ({"category_id": None}, "category_id"),
    ({"category_id": 9999}, "category_id"),
    ({"content_markdown": "  "}, "content_markdown"),
    ({"external_ref": "SCH001"}, "external_ref"),       # already used by seeded data
    ({"external_ref": "bad ref!"}, "external_ref"),
    ({"keywords": ["x" * 61]}, "keywords"),
    ({"source_ref": "s" * 501}, "source_ref"),
])
def test_entry_validation(admin_client, overrides, field):
    body = {"title": "Valid title", "category_id": cat_id(), "content_markdown": "Some info", **overrides}
    response = admin_client.post(API, json=body)
    assert response.status_code == 422 and field in response.get_json()["fields"]


def test_update_and_toggles(admin_client):
    data = create(admin_client)
    updated = admin_client.put(f"{API}/{data['id']}", json={
        "title": "Biaya Study Tour", "category_id": cat_id(), "content_markdown": "Rp 400.000", "keywords": []}).get_json()
    assert updated["title"] == "Biaya Study Tour" and updated["external_ref"] is None
    toggled = admin_client.patch(f"{API}/{data['id']}", json={"available_to_ai": False}).get_json()
    assert toggled["available_to_ai"] is False and toggled["used_by_chat"] is False
    assert admin_client.get(f"{API}/summary").get_json()["hidden"] == 1


def test_list_filters_and_search(admin_client):
    create(admin_client)
    create(admin_client, title="Draft entry", external_ref=None, is_published=False, keywords=["draftword"])
    assert admin_client.get(f"{API}?usage=draft").get_json()["total"] == 1
    assert admin_client.get(f"{API}?usage=hidden").get_json()["total"] == 1
    assert admin_client.get(f"{API}?usage=chat").get_json()["total"] == 12  # 11 seeded + 1
    assert admin_client.get(f"{API}?q=draftword").get_json()["total"] == 1   # keywords are searchable
    assert admin_client.get(f"{API}?q=SCH012").get_json()["total"] == 1      # reference is searchable
    assert admin_client.get(f"{API}?category={cat_id('schedule')}").get_json()["total"] == 5


def test_delete_entry(admin_client):
    data = create(admin_client)
    assert admin_client.delete(f"{API}/{data['id']}").status_code == 204
    assert admin_client.get(f"{API}/{data['id']}").status_code == 404


# --- Documents -------------------------------------------------------------------------------

@pytest.mark.parametrize("name,data,kind,text", [
    ("brosur.pdf", make_pdf("Rundown studi wisata Bandung"), "pdf", "Rundown studi wisata Bandung"),
    ("info.docx", make_docx(["Paragraf satu", "Paragraf dua"]), "docx", "Paragraf satu\nParagraf dua"),
    ("catatan.txt", "Baris satu\r\nBaris dua".encode(), "txt", "Baris satu\nBaris dua"),
    ("panduan.md", "# Judul\n\n- poin".encode(), "md", "# Judul\n\n- poin"),
])
def test_document_upload_and_text_extraction(admin_client, name, data, kind, text):
    entry = create(admin_client)
    result = upload(admin_client, entry["id"], data, name).get_json()
    assert result["document"] == {"name": name, "type": kind, "size": len(data)}
    assert result["document_text_preview"] == text


def test_document_text_is_searchable(admin_client):
    entry = create(admin_client)
    upload(admin_client, entry["id"], make_pdf("Rundown Bandung Lembang"), "rundown.pdf")
    assert admin_client.get(f"{API}?q=lembang").get_json()["total"] == 1
    assert admin_client.get(f"{API}?document=yes").get_json()["total"] == 1


def test_document_download_is_private_attachment(app, admin_client, client, make_user):
    entry = create(admin_client)
    data = make_docx(["Rahasia"])
    upload(admin_client, entry["id"], data, "info.docx")
    response = admin_client.get(f"{API}/{entry['id']}/document")
    assert response.status_code == 200 and response.data == data
    assert response.headers["Content-Disposition"].startswith("attachment")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    response.close()

    anonymous = app.test_client()
    assert anonymous.get(f"{API}/{entry['id']}/document").status_code == 401
    student = app.test_client()
    login(student, make_user("user").email)
    assert student.get(f"{API}/{entry['id']}/document").status_code == 403
    # not reachable through the public /media route either
    stored = db.session.get(KnowledgeEntry, entry["id"]).document_path.split("/")[1]
    assert anonymous.get(f"/media/knowledge/{stored}").status_code == 404


def test_replace_and_remove_document_deletes_files(admin_client):
    entry = create(admin_client)
    upload(admin_client, entry["id"], b"first", "a.txt")
    upload(admin_client, entry["id"], b"second", "b.txt")
    assert len(list(documents_dir().iterdir())) == 1  # old file deleted on replace
    assert admin_client.delete(f"{API}/{entry['id']}/document").status_code == 204
    assert list(documents_dir().iterdir()) == []
    assert admin_client.get(f"{API}/{entry['id']}").get_json()["document"] is None


def test_deleting_entry_deletes_its_document(admin_client):
    entry = create(admin_client)
    upload(admin_client, entry["id"], b"text", "a.txt")
    admin_client.delete(f"{API}/{entry['id']}")
    assert list(documents_dir().iterdir()) == []


def _zip_without_word():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("hello.txt", "hi")
    return out.getvalue()


@pytest.mark.parametrize("name,data", [
    ("page.html", b"<html><script>alert(1)</script></html>"),
    ("fake.pdf", b"this is not a pdf"),
    ("archive.docx", _zip_without_word()),
    ("binary.txt", b"abc\x00def"),
    ("latin.txt", "caf\xe9".encode("latin-1")),
    ("empty.txt", b""),
    ("image.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20),
])
def test_bad_documents_rejected(admin_client, name, data):
    entry = create(admin_client)
    response = upload(admin_client, entry["id"], data, name)
    assert response.status_code == 422 and "document" in response.get_json()["fields"]
    assert list(documents_dir().iterdir()) == []


def test_document_too_large(admin_client):
    entry = create(admin_client)
    response = upload(admin_client, entry["id"], b"a" * (10 * 1024 * 1024 + 1), "big.txt")
    assert response.status_code in (413, 422)


# --- Match tester & chat integration ------------------------------------------------------------

def test_match_tester(admin_client):
    entry = create(admin_client)
    result = admin_client.post(f"{API}/test-match", json={"question": "Berapa biaya studi wisata?"}).get_json()
    assert result["answer"]["id"] == entry["id"]
    assert result["candidates"][0]["keyword_match"] is True


def test_match_tester_shows_hidden_candidates(admin_client):
    entry = create(admin_client, available_to_ai=False)
    result = admin_client.post(f"{API}/test-match", json={"question": "Berapa biaya studi wisata?"}).get_json()
    assert result["answer"] is None or result["answer"]["id"] != entry["id"]
    hidden = next(c for c in result["candidates"] if c["id"] == entry["id"])
    assert hidden["used_by_chat"] is False


def test_chat_uses_new_entry_and_respects_visibility(admin_client):
    entry = create(admin_client)
    reply = ask(admin_client, "Berapa biaya studi wisata?")
    assert "Rp 350.000" in reply["html"] and reply["source"]["title"] == "Biaya Studi Wisata"
    admin_client.patch(f"{API}/{entry['id']}", json={"is_published": False})
    assert "Rp 350.000" not in ask(admin_client, "Berapa biaya studi wisata?")["html"]


# --- Permissions & pages -------------------------------------------------------------------------

def test_permissions(client, make_user):
    login(client, make_user("viewer").email)
    assert client.get(API).status_code == 200
    assert client.post(API, json={"title": "x"}).status_code == 403
    assert client.get("/admin/knowledge/new").status_code == 403
    assert b"New entry" not in client.get("/admin/knowledge").data


def test_content_manager_can_edit(client, make_user):
    login(client, make_user("content_manager").email)
    assert create(client)["created_by"] == "Content_Manager"


def test_pages(admin_client):
    entry = create(admin_client)
    assert admin_client.get("/admin/knowledge").status_code == 200
    page = admin_client.get(f"/admin/knowledge/{entry['id']}")
    assert page.status_code == 200 and b"Biaya Studi Wisata" in page.data and b'aria-current="page"' in page.data
    assert admin_client.get("/admin/knowledge/new").status_code == 200
    assert admin_client.get("/admin/knowledge/99999").status_code == 404
