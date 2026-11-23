"""Custom `flask` commands: `flask seed` and `flask create-admin`."""

import os

import click
from sqlalchemy import select

from .extensions import db
from .models import Role, User
from .seeds import create_admin, seed_all
from .services.audit import log_activity


def register_cli(app):
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
