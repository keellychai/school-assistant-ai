from flask import render_template
from flask_login import current_user

from . import bp


@bp.route("/")
def index():
    # Temporary landing page; Step 3 replaces it with the full dashboard layout.
    permissions = sorted(p.code for p in current_user.role.permissions)
    return render_template("admin/index.html", permissions=permissions)
