"""props and environments with their guards (SRS 12, Phase 7)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def _guards(owner: str, version: str, fk: str, fixed: str, label: str) -> str:
    """The same rules as every other library asset: a published version never changes or
    goes away, a version in review keeps its build, codes and identity never change."""
    return rf"""
CREATE FUNCTION studio_guard_{version}() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    st text;
    probe {version}s;
BEGIN
    SELECT status INTO st FROM asset_versions_registry WHERE id = OLD.id;
    IF TG_OP = 'DELETE' THEN
        IF st IN ('published', 'deprecated') THEN
            RAISE EXCEPTION '{label} version % is %, it cannot be deleted', OLD.id, st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.id <> OLD.id OR NEW.{fk} <> OLD.{fk}
       OR NEW.based_on_version_id IS DISTINCT FROM OLD.based_on_version_id THEN
        RAISE EXCEPTION '{label} version identity columns are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    probe := NEW;
    probe.updated_at := OLD.updated_at;
    IF st IN ('published', 'deprecated') THEN
        probe.deprecated_at := OLD.deprecated_at;
        IF probe IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION '{label} version % is %, it cannot be changed', OLD.id, st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF st = 'review' THEN
        probe.review_decision := OLD.review_decision; probe.review_notes := OLD.review_notes;
        probe.reviewed_by := OLD.reviewed_by; probe.reviewed_at := OLD.reviewed_at;
        probe.manifest_key := OLD.manifest_key; probe.manifest_sha256 := OLD.manifest_sha256;
        probe.published_by := OLD.published_by; probe.published_at := OLD.published_at;
        IF probe IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION '{label} version % is in review, its build cannot change', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER {version}s_guard
    BEFORE UPDATE OR DELETE ON {version}s
    FOR EACH ROW EXECUTE FUNCTION studio_guard_{version}();

CREATE FUNCTION studio_guard_{owner}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.code <> OLD.code OR NEW.id <> OLD.id THEN
        RAISE EXCEPTION '{label} codes never change' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.{fixed} <> OLD.{fixed} THEN
        RAISE EXCEPTION 'a {label}''s {fixed} never changes; make a new {label}'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.recommended_version_id IS DISTINCT FROM OLD.recommended_version_id
       AND NEW.recommended_version_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM {version}s v JOIN asset_versions_registry r ON r.id = v.id
                       WHERE v.id = NEW.recommended_version_id AND v.{fk} = NEW.id
                         AND r.status = 'published') THEN
        RAISE EXCEPTION 'the recommended version must be a published version of this {label}'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER {owner}s_guard
    BEFORE UPDATE ON {owner}s
    FOR EACH ROW EXECUTE FUNCTION studio_guard_{owner}();
"""


def _drop(owner: str, version: str) -> str:
    return f"""
DROP TRIGGER IF EXISTS {owner}s_guard ON {owner}s;
DROP TRIGGER IF EXISTS {version}s_guard ON {version}s;
DROP FUNCTION IF EXISTS studio_guard_{owner}();
DROP FUNCTION IF EXISTS studio_guard_{version}();
"""


KINDS = (("prop", "prop_version", "prop_id", "category", "prop"),
         ("environment", "environment_version", "environment_id", "template_key", "environment"))


def upgrade() -> None:
    op.create_table('environments',
    sa.Column('template_key', sa.String(length=32), nullable=False, comment='living_room|bedroom|classroom|park|street'),
    sa.Column('kind', sa.String(length=8), nullable=False, comment='room|outdoor'),
    sa.Column('recommended_version_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('lifecycle', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_environments_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_environments')),
    sa.UniqueConstraint('code', name=op.f('uq_environments_code'))
    )
    op.create_index(op.f('ix_environments_created_by'), 'environments', ['created_by'], unique=False)
    op.create_index(op.f('ix_environments_name'), 'environments', ['name'], unique=False)
    op.create_table('props',
    sa.Column('category', sa.String(length=16), nullable=False, comment='furniture|household|toy|nature|vehicle|food'),
    sa.Column('recommended_version_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('lifecycle', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_props_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_props')),
    sa.UniqueConstraint('code', name=op.f('uq_props_code'))
    )
    op.create_index(op.f('ix_props_created_by'), 'props', ['created_by'], unique=False)
    op.create_index(op.f('ix_props_name'), 'props', ['name'], unique=False)
    op.create_table('environment_versions',
    sa.Column('environment_id', sa.UUID(), nullable=False),
    sa.Column('based_on_version_id', sa.UUID(), nullable=True),
    sa.Column('template_ref', sa.String(length=64), nullable=True, comment='ENVD-LIVING_ROOM@0.1'),
    sa.Column('lighting', sa.String(length=32), nullable=True, comment='lighting preset'),
    sa.Column('layout', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='ResolvedEnvironment.layout(): solids, tops, seats, marks, cameras'),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('spec_digest', sa.String(length=64), nullable=False, comment='resolved digest of spec'),
    sa.Column('built_digest', sa.String(length=64), nullable=True, comment='digest the current files were built from'),
    sa.Column('style_profile_ref', sa.String(length=64), nullable=True),
    sa.Column('catalog_ref', sa.String(length=64), nullable=True),
    sa.Column('size', postgresql.ARRAY(sa.Float()), nullable=False, comment='width, depth, height in metres'),
    sa.Column('check_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='measured check of the linked file'),
    sa.Column('blender_version', sa.String(length=32), nullable=True),
    sa.Column('review_decision', sa.String(length=16), nullable=True, comment='approve|reject'),
    sa.Column('review_notes', sa.Text(), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('manifest_key', sa.Text(), nullable=True),
    sa.Column('manifest_sha256', sa.String(length=64), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deprecated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('generate_job_id', sa.UUID(), nullable=True),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('published_by', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['based_on_version_id'], ['environment_versions.id'], name=op.f('fk_environment_versions_based_on_version_id_environment_versions')),
    sa.ForeignKeyConstraint(['environment_id'], ['environments.id'], name=op.f('fk_environment_versions_environment_id_environments')),
    sa.ForeignKeyConstraint(['generate_job_id'], ['jobs.id'], name=op.f('fk_environment_versions_generate_job_id_jobs')),
    sa.ForeignKeyConstraint(['id'], ['asset_versions_registry.id'], name=op.f('fk_environment_versions_id_asset_versions_registry'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], name=op.f('fk_environment_versions_published_by_users')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_environment_versions_reviewed_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_environment_versions'))
    )
    op.create_index(op.f('ix_environment_versions_environment_id'), 'environment_versions', ['environment_id'], unique=False)
    op.create_table('prop_versions',
    sa.Column('prop_id', sa.UUID(), nullable=False),
    sa.Column('based_on_version_id', sa.UUID(), nullable=True),
    sa.Column('item_ref', sa.String(length=64), nullable=True, comment='PROPD-CHAIR@0.1'),
    sa.Column('solid', sa.Boolean(), nullable=False, comment='characters walk around it'),
    sa.Column('rests_on', sa.String(length=8), nullable=False, comment='floor|top'),
    sa.Column('top_z', sa.Float(), nullable=True, comment='height of its top, if things can stand on it'),
    sa.Column('seat_z', sa.Float(), nullable=True, comment='height of its seat, if it has one'),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('spec_digest', sa.String(length=64), nullable=False, comment='resolved digest of spec'),
    sa.Column('built_digest', sa.String(length=64), nullable=True, comment='digest the current files were built from'),
    sa.Column('style_profile_ref', sa.String(length=64), nullable=True),
    sa.Column('catalog_ref', sa.String(length=64), nullable=True),
    sa.Column('size', postgresql.ARRAY(sa.Float()), nullable=False, comment='width, depth, height in metres'),
    sa.Column('check_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='measured check of the linked file'),
    sa.Column('blender_version', sa.String(length=32), nullable=True),
    sa.Column('review_decision', sa.String(length=16), nullable=True, comment='approve|reject'),
    sa.Column('review_notes', sa.Text(), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('manifest_key', sa.Text(), nullable=True),
    sa.Column('manifest_sha256', sa.String(length=64), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deprecated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('generate_job_id', sa.UUID(), nullable=True),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('published_by', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['based_on_version_id'], ['prop_versions.id'], name=op.f('fk_prop_versions_based_on_version_id_prop_versions')),
    sa.ForeignKeyConstraint(['generate_job_id'], ['jobs.id'], name=op.f('fk_prop_versions_generate_job_id_jobs')),
    sa.ForeignKeyConstraint(['id'], ['asset_versions_registry.id'], name=op.f('fk_prop_versions_id_asset_versions_registry'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['prop_id'], ['props.id'], name=op.f('fk_prop_versions_prop_id_props')),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], name=op.f('fk_prop_versions_published_by_users')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_prop_versions_reviewed_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prop_versions'))
    )
    op.create_index(op.f('ix_prop_versions_prop_id'), 'prop_versions', ['prop_id'], unique=False)
    op.create_foreign_key('fk_props_recommended_version_id', 'props', 'prop_versions',
                          ['recommended_version_id'], ['id'])
    op.create_foreign_key('fk_environments_recommended_version_id', 'environments', 'environment_versions',
                          ['recommended_version_id'], ['id'])
    for k in KINDS:
        op.execute(_guards(*k))


def downgrade() -> None:
    for k in KINDS:
        op.execute(_drop(k[0], k[1]))
    op.drop_constraint('fk_environments_recommended_version_id', 'environments', type_='foreignkey')
    op.drop_constraint('fk_props_recommended_version_id', 'props', type_='foreignkey')
    op.drop_index(op.f('ix_prop_versions_prop_id'), table_name='prop_versions')
    op.drop_table('prop_versions')
    op.drop_index(op.f('ix_environment_versions_environment_id'), table_name='environment_versions')
    op.drop_table('environment_versions')
    op.drop_index(op.f('ix_props_name'), table_name='props')
    op.drop_index(op.f('ix_props_created_by'), table_name='props')
    op.drop_table('props')
    op.drop_index(op.f('ix_environments_name'), table_name='environments')
    op.drop_index(op.f('ix_environments_created_by'), table_name='environments')
    op.drop_table('environments')
