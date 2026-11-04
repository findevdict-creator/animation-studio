"""baseline

Revision ID: 0001
Revises: 
Create Date: 2026-11-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('asset_code_counters',
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('next_number', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('kind', name=op.f('pk_asset_code_counters'))
    )
    op.create_table('permissions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_permissions')),
    sa.UniqueConstraint('name', name=op.f('uq_permissions_name'))
    )
    op.create_table('roles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_roles')),
    sa.UniqueConstraint('name', name=op.f('uq_roles_name'))
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False, comment='OIDC subject'),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('external_id', name=op.f('uq_users_external_id'))
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=False)
    op.create_table('asset_versions_registry',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('asset_id', sa.UUID(), nullable=False, comment="the identity row's id"),
    sa.Column('display_code', sa.String(length=32), nullable=False, comment='CHAR-001'),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('major', sa.Integer(), nullable=True),
    sa.Column('minor', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'review', 'published', 'deprecated')", name=op.f('ck_asset_versions_registry_status')),
    sa.CheckConstraint("status IN ('draft','review') OR major IS NOT NULL", name=op.f('ck_asset_versions_registry_published_has_number')),
    sa.CheckConstraint('(major IS NULL) = (minor IS NULL)', name=op.f('ck_asset_versions_registry_version_pair')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_asset_versions_registry_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_asset_versions_registry')),
    sa.UniqueConstraint('asset_id', 'major', 'minor', name='uq_asset_versions_registry_number')
    )
    op.create_index(op.f('ix_asset_versions_registry_asset_id'), 'asset_versions_registry', ['asset_id'], unique=False)
    op.create_index(op.f('ix_asset_versions_registry_created_by'), 'asset_versions_registry', ['created_by'], unique=False)
    op.create_index(op.f('ix_asset_versions_registry_display_code'), 'asset_versions_registry', ['display_code'], unique=False)
    op.create_index(op.f('ix_asset_versions_registry_kind'), 'asset_versions_registry', ['kind'], unique=False)
    op.create_index(op.f('ix_asset_versions_registry_status'), 'asset_versions_registry', ['status'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_kind', sa.String(length=32), nullable=True),
    sa.Column('target_id', sa.UUID(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_audit_logs_actor_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_actor_id'), 'audit_logs', ['actor_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_at'), 'audit_logs', ['at'], unique=False)
    op.create_index(op.f('ix_audit_logs_target_id'), 'audit_logs', ['target_id'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('queue', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=True),
    sa.Column('progress', sa.Float(), nullable=False),
    sa.Column('progress_message', sa.Text(), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('parent_job_id', sa.UUID(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('requested_by', sa.UUID(), nullable=True),
    sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('enqueued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('worker_host', sa.String(length=255), nullable=True),
    sa.Column('blender_version', sa.String(length=32), nullable=True),
    sa.Column('log_key', sa.Text(), nullable=True, comment='storage key of the captured process log'),
    sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancel_requested', 'cancelled', 'timed_out')", name=op.f('ck_jobs_status')),
    sa.CheckConstraint('progress >= 0 AND progress <= 1', name=op.f('ck_jobs_progress')),
    sa.ForeignKeyConstraint(['parent_job_id'], ['jobs.id'], name=op.f('fk_jobs_parent_job_id_jobs')),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_jobs_requested_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_jobs_idempotency_key'))
    )
    op.create_index(op.f('ix_jobs_parent_job_id'), 'jobs', ['parent_job_id'], unique=False)
    op.create_index(op.f('ix_jobs_requested_by'), 'jobs', ['requested_by'], unique=False)
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)
    op.create_index(op.f('ix_jobs_type'), 'jobs', ['type'], unique=False)
    op.create_table('role_permissions',
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('permission_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name=op.f('fk_role_permissions_permission_id_permissions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_role_permissions_role_id_roles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_id', 'permission_id', name=op.f('pk_role_permissions'))
    )
    op.create_index(op.f('ix_role_permissions_permission_id'), 'role_permissions', ['permission_id'], unique=False)
    op.create_table('user_roles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_user_roles_role_id_roles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_roles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role_id', name=op.f('pk_user_roles'))
    )
    op.create_index(op.f('ix_user_roles_role_id'), 'user_roles', ['role_id'], unique=False)
    op.create_table('asset_dependencies',
    sa.Column('from_version_id', sa.UUID(), nullable=False),
    sa.Column('to_version_id', sa.UUID(), nullable=False),
    sa.Column('dependency_type', sa.String(length=32), nullable=False),
    sa.CheckConstraint('from_version_id <> to_version_id', name=op.f('ck_asset_dependencies_no_self')),
    sa.ForeignKeyConstraint(['from_version_id'], ['asset_versions_registry.id'], name=op.f('fk_asset_dependencies_from_version_id_asset_versions_registry'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_version_id'], ['asset_versions_registry.id'], name=op.f('fk_asset_dependencies_to_version_id_asset_versions_registry'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('from_version_id', 'to_version_id', 'dependency_type', name=op.f('pk_asset_dependencies'))
    )
    op.create_index(op.f('ix_asset_dependencies_to_version_id'), 'asset_dependencies', ['to_version_id'], unique=False)
    op.create_table('asset_files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('version_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('mime_type', sa.String(length=127), nullable=False),
    sa.Column('blender_version', sa.String(length=32), nullable=True),
    sa.Column('created_by_job_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('blend', 'spec', 'thumbnail', 'turntable', 'glb', 'texture', 'manifest', 'log', 'report')", name=op.f('ck_asset_files_role')),
    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_asset_files_sha256_hex')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_asset_files_size')),
    sa.ForeignKeyConstraint(['created_by_job_id'], ['jobs.id'], name=op.f('fk_asset_files_created_by_job_id_jobs')),
    sa.ForeignKeyConstraint(['version_id'], ['asset_versions_registry.id'], name=op.f('fk_asset_files_version_id_asset_versions_registry'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_asset_files')),
    sa.UniqueConstraint('version_id', 'storage_key', name='uq_asset_files_version_key')
    )
    op.create_index(op.f('ix_asset_files_created_by_job_id'), 'asset_files', ['created_by_job_id'], unique=False)
    op.create_index(op.f('ix_asset_files_sha256'), 'asset_files', ['sha256'], unique=False)
    op.create_index(op.f('ix_asset_files_version_id'), 'asset_files', ['version_id'], unique=False)
    op.create_table('job_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False, comment='status|progress|log|error'),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_job_events_job_id_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_events'))
    )
    op.create_index(op.f('ix_job_events_job_id'), 'job_events', ['job_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_job_events_job_id'), table_name='job_events')
    op.drop_table('job_events')
    op.drop_index(op.f('ix_asset_files_version_id'), table_name='asset_files')
    op.drop_index(op.f('ix_asset_files_sha256'), table_name='asset_files')
    op.drop_index(op.f('ix_asset_files_created_by_job_id'), table_name='asset_files')
    op.drop_table('asset_files')
    op.drop_index(op.f('ix_asset_dependencies_to_version_id'), table_name='asset_dependencies')
    op.drop_table('asset_dependencies')
    op.drop_index(op.f('ix_user_roles_role_id'), table_name='user_roles')
    op.drop_table('user_roles')
    op.drop_index(op.f('ix_role_permissions_permission_id'), table_name='role_permissions')
    op.drop_table('role_permissions')
    op.drop_index(op.f('ix_jobs_type'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_requested_by'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_parent_job_id'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_audit_logs_target_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_actor_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_asset_versions_registry_status'), table_name='asset_versions_registry')
    op.drop_index(op.f('ix_asset_versions_registry_kind'), table_name='asset_versions_registry')
    op.drop_index(op.f('ix_asset_versions_registry_display_code'), table_name='asset_versions_registry')
    op.drop_index(op.f('ix_asset_versions_registry_created_by'), table_name='asset_versions_registry')
    op.drop_index(op.f('ix_asset_versions_registry_asset_id'), table_name='asset_versions_registry')
    op.drop_table('asset_versions_registry')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_table('roles')
    op.drop_table('permissions')
    op.drop_table('asset_code_counters')
