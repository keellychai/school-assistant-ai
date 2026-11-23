from flask import abort, render_template, request
from ..auth.security import permission_required
from ..extensions import db
from ..models import ConfiguredQuestion, ConversationFlow, KnowledgeEntry, User
from . import bp
from .navigation import ALL_ITEMS, nav_lookup, visible_navigation


@bp.context_processor
def inject_layout():
    section, item = nav_lookup(request.endpoint)
    return {
        "admin_nav": visible_navigation(),
        "nav_section": section,
        "nav_item": item,
        # "branding" and "timezone" come from the app-wide context processor (app/__init__.py).
    }


@bp.route("/")
@permission_required("dashboard.view")
def index():
    # The numbers, charts and lists are loaded from /api/admin/dashboard by the page.
    return render_template("admin/index.html")


@bp.route("/activity")
@permission_required("activity.read")
def activity():
    return render_template("admin/activity.html")


# --- AI Chat configuration -------------------------------------------------------
# Pages are thin shells; data is loaded from /api/admin/chat/* by the page script.

@bp.route("/chat/questions")
@permission_required("chatconfig.read")
def questions():
    return render_template("admin/chat/questions.html")


@bp.route("/chat/questions/<int:question_id>")
@permission_required("chatconfig.read")
def question_edit(question_id):
    question = db.session.get(ConfiguredQuestion, question_id) or abort(404)
    return render_template("admin/chat/question_edit.html", question=question)


@bp.route("/chat/categories")
@permission_required("chatconfig.read")
def question_categories():
    return render_template("admin/chat/categories.html")


@bp.route("/chat/templates")
@permission_required("chatconfig.read")
def answer_templates():
    return render_template("admin/chat/templates.html")


@bp.route("/events")
@permission_required("events.read")
def events():
    return render_template("admin/events.html")


@bp.route("/notifications")
@permission_required("notifications.read")
def notifications():
    return render_template("admin/notifications.html")


@bp.route("/knowledge")
@permission_required("knowledge.read")
def knowledge():
    return render_template("admin/knowledge.html")


@bp.route("/knowledge/new")
@permission_required("knowledge.write")
def knowledge_new():
    return render_template("admin/knowledge_edit.html", entry=None)


@bp.route("/knowledge/<int:entry_id>")
@permission_required("knowledge.read")
def knowledge_edit(entry_id):
    entry = db.session.get(KnowledgeEntry, entry_id) or abort(404)
    return render_template("admin/knowledge_edit.html", entry=entry)


@bp.route("/users")
@permission_required("users.read")
def users():
    return render_template("admin/users.html")


@bp.route("/users/<int:user_id>")
@permission_required("users.read")
def user_detail(user_id):
    user = db.session.get(User, user_id) or abort(404)
    return render_template("admin/user_detail.html", user=user)


@bp.route("/roles")
@permission_required("roles.manage")
def roles():
    return render_template("admin/roles.html")


@bp.route("/assistant")
@permission_required("assistant.read")
def assistant():
    return render_template("admin/assistant.html")


@bp.route("/chat/flows")
@permission_required("chatconfig.read")
def flows():
    return render_template("admin/chat/flows.html")


@bp.route("/chat/flows/new")
@permission_required("chatconfig.write")
def flow_new():
    return render_template("admin/chat/flow_edit.html", flow=None)


@bp.route("/chat/flows/<int:flow_id>")
@permission_required("chatconfig.read")
def flow_edit(flow_id):
    flow = db.session.get(ConversationFlow, flow_id) or abort(404)
    return render_template("admin/chat/flow_edit.html", flow=flow)


@bp.route("/settings")
@permission_required("settings.read")
def settings():
    return render_template("admin/settings.html")


@bp.route("/profile")
@permission_required("dashboard.view")
def profile():
    return render_template("admin/profile.html")


def _placeholder_view(item):
    def view():
        return render_template("admin/coming_soon.html")
    view.__name__ = f"placeholder_{item.endpoint}"
    return view


# Modules not built yet get a protected placeholder page so the navigation is complete.
for _section, _item in ALL_ITEMS.values():
    if _item.planned_step is not None:
        bp.add_url_rule(
            _item.path,
            endpoint=_item.endpoint,
            view_func=permission_required(_item.permission)(_placeholder_view(_item)),
        )
