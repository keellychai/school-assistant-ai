"""add user session token

Revision ID: 7639b70f911f
Revises: b469023c6db0
Create Date: 2026-11-23 12:52:15.274911

"""
import secrets

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7639b70f911f'
down_revision = 'b469023c6db0'
branch_labels = None
depends_on = None


def upgrade():
    # 1) add as nullable, 2) give every existing user a token, 3) make it required + unique.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('session_token', sa.String(length=64), nullable=True))

    conn = op.get_bind()
    users = sa.table('users', sa.column('id', sa.Integer), sa.column('session_token', sa.String))
    for (user_id,) in conn.execute(sa.select(users.c.id)).all():
        conn.execute(
            users.update().where(users.c.id == user_id).values(session_token=secrets.token_hex(32))
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('session_token', existing_type=sa.String(length=64), nullable=False)
        batch_op.create_unique_constraint(batch_op.f('uq_users_session_token'), ['session_token'])


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_users_session_token'), type_='unique')
        batch_op.drop_column('session_token')
