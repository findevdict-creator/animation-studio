"""accessory identity and versions with their guards (SRS 8)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


GUARDS = r"""
CREATE FUNCTION studio_guard_accessory_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    st text;
    probe accessory_versions;
BEGIN
    SELECT status INTO st FROM asset_versions_registry WHERE id = OLD.id;
    IF TG_OP = 'DELETE' THEN
        IF st IN ('published', 'deprecated') THEN
            RAISE EXCEPTION 'accessory version % is %, it cannot be deleted', OLD.id, st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.id <> OLD.id OR NEW.accessory_id <> OLD.accessory_id
       OR NEW.based_on_version_id IS DISTINCT FROM OLD.based_on_version_id THEN
        RAISE EXCEPTION 'accessory version identity columns are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    probe := NEW;
    probe.updated_at := OLD.updated_at;
    IF st IN ('published', 'deprecated') THEN
        probe.deprecated_at := OLD.deprecated_at;
        IF probe IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'accessory version % is %, it cannot be changed', OLD.id, st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF st = 'review' THEN
        probe.review_decision := OLD.review_decision; probe.review_notes := OLD.review_notes;
        probe.reviewed_by := OLD.reviewed_by; probe.reviewed_at := OLD.reviewed_at;
        probe.manifest_key := OLD.manifest_key; probe.manifest_sha256 := OLD.manifest_sha256;
        probe.published_by := OLD.published_by; probe.published_at := OLD.published_at;
        IF probe IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'accessory version % is in review, its build cannot change', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER accessory_versions_guard
    BEFORE UPDATE OR DELETE ON accessory_versions
    FOR EACH ROW EXECUTE FUNCTION studio_guard_accessory_version();

CREATE FUNCTION studio_guard_accessory() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.code <> OLD.code OR NEW.id <> OLD.id THEN
        RAISE EXCEPTION 'accessory codes never change' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.category <> OLD.category THEN
        RAISE EXCEPTION 'an accessory''s category never changes; make a new accessory'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.recommended_version_id IS DISTINCT FROM OLD.recommended_version_id
       AND NEW.recommended_version_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM accessory_versions v JOIN asset_versions_registry r ON r.id = v.id
                       WHERE v.id = NEW.recommended_version_id AND v.accessory_id = NEW.id
                         AND r.status = 'published') THEN
        RAISE EXCEPTION 'the recommended version must be a published version of this accessory'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER accessories_guard
    BEFORE UPDATE ON accessories
    FOR EACH ROW EXECUTE FUNCTION studio_guard_accessory();
"""

DROP_GUARDS = """
DROP TRIGGER IF EXISTS accessories_guard ON accessories;
DROP TRIGGER IF EXISTS accessory_versions_guard ON accessory_versions;
DROP FUNCTION IF EXISTS studio_guard_accessory();
DROP FUNCTION IF EXISTS studio_guard_accessory_version();
"""


def upgrade() -> None:
    op.create_table('accessories',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False, comment='ACC-001'),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('category', sa.String(length=16), nullable=False, comment='eyewear|headwear|wristwear|bag|toy'),
    sa.Column('recommended_version_id', sa.UUID(), nullable=True),
    sa.Column('lifecycle', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_accessories_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accessories')),
    sa.UniqueConstraint('code', name=op.f('uq_accessories_code'))
    )
    op.create_index(op.f('ix_accessories_created_by'), 'accessories', ['created_by'], unique=False)
    op.create_index(op.f('ix_accessories_name'), 'accessories', ['name'], unique=False)
    op.create_table('accessory_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('accessory_id', sa.UUID(), nullable=False),
    sa.Column('based_on_version_id', sa.UUID(), nullable=True),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('spec_digest', sa.String(length=64), nullable=False, comment='ResolvedAccessory digest of spec'),
    sa.Column('built_digest', sa.String(length=64), nullable=True, comment='digest the current files were built from'),
    sa.Column('style_profile_ref', sa.String(length=64), nullable=True),
    sa.Column('rig_profile_ref', sa.String(length=64), nullable=True),
    sa.Column('catalog_ref', sa.String(length=64), nullable=True),
    sa.Column('item_ref', sa.String(length=64), nullable=True, comment='ACCD-GLASSES@0.1'),
    sa.Column('attach_point', sa.String(length=32), nullable=True, comment='default attachment point'),
    sa.Column('attach_points', postgresql.ARRAY(sa.String(length=32)), nullable=False),
    sa.Column('fit_profiles', postgresql.ARRAY(sa.String(length=64)), nullable=False, comment='declared body-fit profiles; empty means all'),
    sa.Column('conflicts_with_slots', postgresql.ARRAY(sa.String(length=16)), nullable=False),
    sa.Column('check_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='attach check on the reference body'),
    sa.Column('blender_version', sa.String(length=32), nullable=True),
    sa.Column('generate_job_id', sa.UUID(), nullable=True),
    sa.Column('review_decision', sa.String(length=16), nullable=True, comment='approve|reject'),
    sa.Column('review_notes', sa.Text(), nullable=False),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('manifest_key', sa.Text(), nullable=True),
    sa.Column('manifest_sha256', sa.String(length=64), nullable=True),
    sa.Column('published_by', sa.UUID(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deprecated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['accessory_id'], ['accessories.id'], name=op.f('fk_accessory_versions_accessory_id_accessories')),
    sa.ForeignKeyConstraint(['based_on_version_id'], ['accessory_versions.id'], name=op.f('fk_accessory_versions_based_on_version_id_accessory_versions')),
    sa.ForeignKeyConstraint(['generate_job_id'], ['jobs.id'], name=op.f('fk_accessory_versions_generate_job_id_jobs')),
    sa.ForeignKeyConstraint(['id'], ['asset_versions_registry.id'], name=op.f('fk_accessory_versions_id_asset_versions_registry'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], name=op.f('fk_accessory_versions_published_by_users')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_accessory_versions_reviewed_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accessory_versions'))
    )
    op.create_index(op.f('ix_accessory_versions_accessory_id'), 'accessory_versions', ['accessory_id'], unique=False)
    op.create_foreign_key('fk_accessories_recommended_version_id', 'accessories', 'accessory_versions',
                          ['recommended_version_id'], ['id'])
    op.execute(GUARDS)


def downgrade() -> None:
    op.execute(DROP_GUARDS)
    op.drop_constraint('fk_accessories_recommended_version_id', 'accessories', type_='foreignkey')
    op.drop_index(op.f('ix_accessory_versions_accessory_id'), table_name='accessory_versions')
    op.drop_table('accessory_versions')
    op.drop_index(op.f('ix_accessories_name'), table_name='accessories')
    op.drop_index(op.f('ix_accessories_created_by'), table_name='accessories')
    op.drop_table('accessories')
