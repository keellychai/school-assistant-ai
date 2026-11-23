"""Custom `flask` commands: `flask seed` and `flask create-admin`."""

import os
import time

import click
from flask.cli import AppGroup
from sqlalchemy import select

from .extensions import db
from .models import Role, User
from .seeds import create_admin, seed_all
from .services.audit import log_activity
from .services.notifications import dispatch_due


notifications_cli = AppGroup("notifications", help="Deliver scheduled notifications and event reminders.")


@notifications_cli.command("dispatch")
def dispatch_command():
    """Deliver everything that is due, once (suitable for a scheduled task / cron)."""
    stats = dispatch_due()
    click.echo(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in stats.items()))


@notifications_cli.command("worker")
@click.option("--interval", default=60, show_default=True, type=click.IntRange(10, 3600),
              help="Seconds between checks.")
def worker_command(interval):
    """Keep delivering due notifications every INTERVAL seconds (Ctrl+C to stop)."""
    click.echo(f"Notification worker started (every {interval}s). Press Ctrl+C to stop.")
    try:
        while True:
            try:
                stats = dispatch_due()
                if stats["announcements"] or stats["reminders"] or stats["expired_reminders"]:
                    click.echo(f"{time.strftime('%H:%M:%S')} delivered: {stats}")
            except Exception as error:  # noqa: BLE001 - keep the worker alive; next run retries
                db.session.rollback()
                click.echo(f"{time.strftime('%H:%M:%S')} dispatch failed: {error}", err=True)
            finally:
                db.session.remove()
            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("Worker stopped.")


def register_cli(app):
    app.cli.add_command(notifications_cli)

    @app.cli.command("seed")
    @click.option("--no-sample", is_flag=True, help="Only roles, permissions and settings; skip sample content.")
    def seed_command(no_sample):
        """Create roles, permissions, default settings and sample content (safe to re-run)."""
        counts = seed_all(sample=not no_sample)
        click.echo("Roles, permissions and settings are in place.")
        for name, count in counts.items():
            click.echo(f"  {name}: {count} added")

        email, password = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
        has_super_admin = db.session.scalar(
            select(User.id).join(Role).where(Role.name == "super_admin").limit(1)
        )
        if email and password and not has_super_admin:
            user, _ = create_admin(email, password, os.getenv("ADMIN_NAME", "Administrator"))
            log_activity("create", user, summary="Initial super admin created by seed")
            db.session.commit()
            click.echo(f"Super admin created: {user.email}")
        elif not has_super_admin:
            click.echo("No super admin yet. Run `flask create-admin` or set ADMIN_EMAIL/ADMIN_PASSWORD in .env.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", "full_name", prompt="Full name", default="Administrator")
    @click.option("--role", "role_name", default="super_admin", show_default=True,
                  type=click.Choice(["super_admin", "admin", "content_manager", "viewer"]))
    @click.password_option()
    def create_admin_command(email, full_name, role_name, password):
        """Create an administrator account."""
        try:
            user, created = create_admin(email, password, full_name, role_name)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        if not created:
            raise click.ClickException(f"A user with email {user.email} already exists.")
        log_activity("create", user, summary=f"Admin account created via CLI ({role_name})")
        db.session.commit()
        click.echo(f"Created {role_name}: {user.email}")
