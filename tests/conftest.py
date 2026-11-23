import pytest
from flask import g
from flask.testing import FlaskClient

from app import create_app
from app.extensions import db
from app.seeds import create_admin, seed_all

PASSWORD = "test-password-123"


class FreshRequestClient(FlaskClient):
    """Tests keep one app context open, so `g` would survive between requests.
    On a real server every request starts fresh; clear Flask-Login's cached user to match."""

    def open(self, *args, **kwargs):
        g.pop("_login_user", None)
        return super().open(*args, **kwargs)


@pytest.fixture()
def app():
    app = create_app("testing")
    app.test_client_class = FreshRequestClient
    with app.app_context():
        db.create_all()
        seed_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_user(app):
    """make_user("content_manager") -> User with password PASSWORD."""
    counter = {"n": 0}

    def _make(role="super_admin", email=None, **fields):
        counter["n"] += 1
        email = email or f"{role}{counter['n']}@example.com"
        user, _ = create_admin(email, PASSWORD, full_name=fields.pop("full_name", role.title()), role_name=role)
        for key, value in fields.items():
            setattr(user, key, value)
        db.session.commit()
        return user

    return _make


def login(client, email, password=PASSWORD, **extra):
    return client.post("/auth/login", data={"email": email, "password": password, **extra})


@pytest.fixture()
def admin_client(client, make_user):
    login(client, make_user("super_admin").email)
    return client


@pytest.fixture()
def user_client(client, make_user):
    login(client, make_user("user").email)
    return client
