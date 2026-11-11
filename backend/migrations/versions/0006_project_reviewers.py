"""project reviewers

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-30 09:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('project_reviewers',
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('reviewer_id', sa.Integer(), nullable=False),
    sa.Column('assigned_by', sa.Integer(), nullable=True),
    sa.Column('assigned_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('project_id')
    )
    with op.batch_alter_table('project_reviewers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_reviewers_reviewer_id'), ['reviewer_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('project_reviewers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_project_reviewers_reviewer_id'))

    op.drop_table('project_reviewers')
