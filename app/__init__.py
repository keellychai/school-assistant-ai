"""SchoolSync AI application factory."""

import os

from flask import Flask, jsonify, render_template, request

from .config import CONFIGS, INSTANCE_DIR
from .extensions import csrf, db, limiter, login_manager, migrate


def create_app(config_name=None):
    config_name = config_name or os.getenv("APP_ENV", "development")
    if config_name not in CONFIGS:
        raise RuntimeError(f"Unknown APP_ENV '{config_name}'. Use one of: {', '.join(CONFIGS)}")

    app = Flask(__name__, instance_path=str(INSTANCE_DIR), instance_relative_config=True)
    app.config.from_object(CONFIGS[config_name])

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is not set. Copy .env.example to .env and fill it in "
            "(generate one with: python -c \"import secrets; print(secrets.token_hex(32))\")."
        )

    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_FOLDER"].mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)  # batch mode = ALTER support on SQLite
    login_manager.init_app(app)
    login_manager.session_protection = "strong"
    csrf.init_app(app)
    limiter.init_app(app)

    from . import models  # noqa: F401  (registers every model with SQLAlchemy)

    from .main import bp as main_bp
    from .auth import bp as auth_bp
    from .admin import bp as admin_bp
    from .api.admin import bp as admin_api_bp
    from .api.me import bp as me_api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_api_bp)
    app.register_blueprint(me_api_bp)

    from .cli import register_cli
    register_cli(app)

    _register_error_handlers(app)
    return app


def _register_error_handlers(app):
    def wants_json():
        return request.path.startswith("/api/")

    for code, message in {
        400: "Bad request.",
        401: "Authentication required.",
        403: "You do not have permission to do that.",
        404: "Not found.",
        405: "Method not allowed.",
        413: "File is too large.",
        429: "Too many requests. Please slow down.",
        500: "Internal server error.",
    }.items():
        def handler(error, code=code, message=message):
            if code == 500:
                db.session.rollback()
            if wants_json():
                return jsonify({"error": message}), code
            return render_template("errors/error.html", code=code, message=message), code

        app.register_error_handler(code, handler)
