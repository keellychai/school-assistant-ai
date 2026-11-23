from datetime import timedelta

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db, limiter
from ..models import User
from ..models.base import utcnow
from ..services import system_settings
from ..services.audit import log_activity
from . import bp
from .forms import LoginForm
from .security import home_url_for, is_safe_next

GENERIC_ERROR = "Incorrect email or password."

# Hash checked when the email doesn't exist, so the response takes as long as
# a real password check (stops attackers discovering which emails are registered).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _lockout_policy():
    return system_settings.lockout_policy()


def _register_failure(user):
    max_attempts, minutes = _lockout_policy()
    user.failed_login_attempts += 1
    log_activity("login_failed", user, actor=user,
                 summary=f"Wrong password ({user.failed_login_attempts}/{max_attempts})")
    if user.failed_login_attempts >= max_attempts:
        user.locked_until = utcnow() + timedelta(minutes=minutes)
        user.failed_login_attempts = 0
        log_activity("account_locked", user, actor=user, summary=f"Locked for {minutes} minutes")
    db.session.commit()


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(home_url_for(current_user))

    form = LoginForm()
    next_path = request.args.get("next")

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.scalar(select(User).where(User.email == email))

        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data)
            log_activity("login_failed", entity_type="users", summary=f"Unknown email: {email[:120]}")
            db.session.commit()
            flash(GENERIC_ERROR, "error")
        elif user.is_locked:
            _, minutes = _lockout_policy()
            log_activity("login_blocked", user, actor=user, summary="Login attempt while locked")
            db.session.commit()
            flash(f"Too many failed attempts. Try again in up to {minutes} minutes.", "error")
        elif not user.check_password(form.password.data):
            _register_failure(user)
            flash(GENERIC_ERROR, "error")
        elif not user.is_active:
            # Only revealed after a correct password, so it doesn't leak account status.
            log_activity("login_blocked", user, actor=user, summary="Login attempt on deactivated account")
            db.session.commit()
            flash("This account has been deactivated. Contact an administrator.", "error")
        else:
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login_at = utcnow()
            session.clear()  # drop anything from before login (prevents session fixation)
            login_user(user, remember=form.remember.data)
            session.permanent = True
            log_activity("login", user, actor=user, summary="Signed in")
            db.session.commit()
            return redirect(next_path if is_safe_next(next_path) else home_url_for(user))

    return render_template("index.html", form=form, next_path=next_path), 200


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_activity("logout", current_user, summary="Signed out")
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))
