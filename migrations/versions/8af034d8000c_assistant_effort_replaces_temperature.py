"""assistant effort replaces temperature

Current Claude models reject sampling parameters (temperature/top_p/top_k);
response depth is controlled with an effort level instead. Also fills in the
provider defaults and raises max_tokens (it includes the model's thinking, so
the old 512 would cut answers off).

Revision ID: 8af034d8000c
Revises: 7639b70f911f
Create Date: 2026-11-23

"""
from alembic import op
import sqlalchemy as sa


revision = '8af034d8000c'
down_revision = '7639b70f911f'
branch_labels = None
depends_on = None

settings = sa.table(
    "assistant_settings",
    sa.column("ai_provider", sa.String), sa.column("ai_model", sa.String),
    sa.column("api_key_env_var", sa.String), sa.column("max_tokens", sa.Integer),
)


def upgrade():
    with op.batch_alter_table('assistant_settings', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_assistant_settings_temperature_range'), type_='check')
        batch_op.drop_column('temperature')
        batch_op.add_column(sa.Column('ai_effort', sa.String(length=10), nullable=False, server_default='medium'))
        batch_op.create_check_constraint(
            'valid_effort', "ai_effort IN ('low', 'medium', 'high', 'xhigh', 'max')")
        batch_op.alter_column('max_tokens', existing_type=sa.Integer(), server_default='16000')

    op.execute(settings.update().where(settings.c.ai_provider.is_(None)).values(ai_provider="anthropic"))
    op.execute(settings.update().where(settings.c.ai_model.is_(None)).values(ai_model="claude-opus-5-5"))
    op.execute(settings.update().where(settings.c.api_key_env_var.is_(None)).values(api_key_env_var="ANTHROPIC_API_KEY"))
    op.execute(settings.update().where(settings.c.max_tokens < 1024).values(max_tokens=16000))


def downgrade():
    with op.batch_alter_table('assistant_settings', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_assistant_settings_valid_effort'), type_='check')
        batch_op.drop_column('ai_effort')
        batch_op.add_column(sa.Column('temperature', sa.Float(), nullable=False, server_default='0.3'))
        batch_op.create_check_constraint('temperature_range', 'temperature >= 0 AND temperature <= 2')
        batch_op.alter_column('max_tokens', existing_type=sa.Integer(), server_default='512')
