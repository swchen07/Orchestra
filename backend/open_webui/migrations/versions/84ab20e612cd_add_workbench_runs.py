"""Durable workbench runs and replayable events.

Revision ID: 84ab20e612cd
Revises: 0bd252b206fb
"""
from alembic import op
import sqlalchemy as sa

revision = '84ab20e612cd'
down_revision = '0bd252b206fb'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('workbench_run',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.Text(), nullable=False),
        sa.Column('harness_id', sa.Text(), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('timeout_seconds', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('started_at', sa.Float()),
        sa.Column('ended_at', sa.Float()),
        sa.Column('error_code', sa.Text()),
        sa.Column('sequence', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_workbench_run_owner_id', 'workbench_run', ['owner_id'])
    op.create_table('workbench_event',
        sa.Column('run_id', sa.String(36), sa.ForeignKey('workbench_run.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('sequence', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False))


def downgrade():
    op.drop_table('workbench_event')
    op.drop_index('ix_workbench_run_owner_id', table_name='workbench_run')
    op.drop_table('workbench_run')
