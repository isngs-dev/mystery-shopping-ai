"""fixed error taxonomy: error_subtype on rule versions and findings

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('rule_versions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('error_subtype', sa.String(length=40), nullable=True))
        batch_op.create_index(batch_op.f('ix_rule_versions_error_subtype'), ['error_subtype'], unique=False)
    with op.batch_alter_table('findings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('error_subtype', sa.String(length=40), nullable=True))
        batch_op.create_index(batch_op.f('ix_findings_error_subtype'), ['error_subtype'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('findings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_findings_error_subtype'))
        batch_op.drop_column('error_subtype')
    with op.batch_alter_table('rule_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rule_versions_error_subtype'))
        batch_op.drop_column('error_subtype')
