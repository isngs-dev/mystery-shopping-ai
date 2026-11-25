"""extension: learned guideline files, password reset codes, picture requirements

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-25 14:00:00

These tables were added with the extension features; in production only Alembic creates tables (create_all is skipped), so without this
migration the guideline learning, the Drive import, "Forgot password" and the picture check fail with "relation does not exist".
Every step first checks whether the table is already there (a development database made by create_all).
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def _missing(name: str) -> bool:
    return name not in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _missing('web_guidelines'):
        op.create_table('web_guidelines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('filename', sa.String(length=200), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('projects', sa.JSON(), nullable=False),
        sa.Column('text_chars', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('rule_keys', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('learned_by', sa.Integer(), nullable=True),
        sa.Column('learned_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
        sa.ForeignKeyConstraint(['learned_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_id', 'sha256'))
        op.create_index('ix_web_guidelines_client_id', 'web_guidelines', ['client_id'])
    if _missing('password_resets'):
        op.create_table('password_resets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
        op.create_index('ix_password_resets_user_id', 'password_resets', ['user_id'])
        op.create_index('ix_password_resets_created_at', 'password_resets', ['created_at'])
    if _missing('web_photo_rules'):
        op.create_table('web_photo_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('guideline_id', sa.Integer(), nullable=True),
        sa.Column('text', sa.String(length=600), nullable=False),
        sa.Column('scope', sa.String(length=12), nullable=False),
        sa.Column('sha16', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
        sa.ForeignKeyConstraint(['guideline_id'], ['web_guidelines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
        op.create_index('ix_web_photo_rules_client_id', 'web_photo_rules', ['client_id'])
        op.create_index('ix_web_photo_rules_guideline_id', 'web_photo_rules', ['guideline_id'])


def downgrade() -> None:
    for name in ('web_photo_rules', 'password_resets', 'web_guidelines'):
        if not _missing(name):
            op.drop_table(name)
