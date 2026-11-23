"""Student-facing pages: dashboard, AI Chat and public media.

The pages are shells; their data comes from /api/chat/* and /api/me/*.
"""

from flask import abort, redirect, render_template, send_from_directory, url_for
from flask_login import current_user, login_required

from ..auth.security import home_url_for
from ..services.uploads import FILENAME_RE, PUBLIC_KINDS, upload_dir
from . import bp


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(home_url_for(current_user))
    return redirect(url_for("auth.login"))


@bp.route("/media/<kind>/<filename>")
def media(kind, filename):
    """Public uploaded images (e.g. the assistant's avatar shown in AI Chat)."""
    if kind not in PUBLIC_KINDS or not FILENAME_RE.match(filename):
        abort(404)
    response = send_from_directory(upload_dir(kind), filename, max_age=3600)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


@bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@bp.route("/chatbot")
@login_required
def chatbot():
    return render_template("chatbot.html")
