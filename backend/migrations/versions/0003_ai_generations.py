"""ai generations log (SRS 24), append-only

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ai_generations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('task', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('prompt_sha256', sa.String(length=64), nullable=False, comment='system + user prompt, images excluded'),
    sa.Column('input_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='params, image keys and hashes'),
    sa.Column('output', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='the validated output, or the last bad one'),
    sa.Column('issues', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('valid', sa.Boolean(), nullable=False),
    sa.Column('stop', sa.String(length=16), nullable=False, comment='ok|refusal|max_tokens|error'),
    sa.Column('repair_turns', sa.Integer(), nullable=False),
    sa.Column('calls', sa.Integer(), nullable=False),
    sa.Column('usage', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_ai_generations_job_id_jobs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_generations'))
    )
    op.create_index(op.f('ix_ai_generations_job_id'), 'ai_generations', ['job_id'], unique=False)
    op.create_index(op.f('ix_ai_generations_task'), 'ai_generations', ['task'], unique=False)
    op.execute("""
CREATE TRIGGER ai_generations_append_only
    BEFORE UPDATE OR DELETE ON ai_generations
    FOR EACH ROW EXECUTE FUNCTION studio_append_only();
""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ai_generations_append_only ON ai_generations")
    op.drop_index(op.f('ix_ai_generations_task'), table_name='ai_generations')
    op.drop_index(op.f('ix_ai_generations_job_id'), table_name='ai_generations')
    op.drop_table('ai_generations')
