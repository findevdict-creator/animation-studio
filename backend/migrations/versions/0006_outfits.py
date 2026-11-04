"""outfit identity, versions and the compatibility registry, with their guards (SRS 7)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


GUARDS = r"""
CREATE FUNCTION studio_guard_outfit_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    st text;
    probe outfit_versions;
BEGIN
    SELECT status INTO st FROM asset_versions_registry WHERE id = OLD.id;
    IF TG_OP = 'DELETE' THEN
        IF st IN ('published', 'deprecated') THEN
            RAISE EXCEPTION 'outfit version % is %, it cannot be deleted', OLD.id, st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.id <> OLD.id OR NEW.outfit_id <> OLD.outfit_id
       OR NEW.based_on_version_id IS DISTINCT FROM OLD.based_on_version_id THEN
        RAISE EXCEPTION 'outfit version identity columns are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    probe := NEW;
    probe.updated_at := OLD.updated_at;
    IF st IN ('published', 'deprecated') THEN
        probe.deprecated_at := OLD.deprecated_at;
        IF probe IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'outfit version % is %, it cannot be changed', OLD.id, st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF st = 'review' THEN
        probe.review_decision := OLD.review_decision; probe.review_notes := OLD.review_notes;
        probe.reviewed_by := OLD.reviewed_by; probe.reviewed_at := OLD.reviewed_at;
        probe.manifest_key := OLD.manifest_key; probe.manifest_sha256 := OLD.manifest_sha256;
        probe.published_by := OLD.published_by; probe.published_at := OLD.published_at;
        IF probe IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'outfit version % is in review, its build cannot change', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER outfit_versions_guard
    BEFORE UPDATE OR DELETE ON outfit_versions
    FOR EACH ROW EXECUTE FUNCTION studio_guard_outfit_version();

CREATE FUNCTION studio_guard_outfit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.code <> OLD.code OR NEW.id <> OLD.id THEN
        RAISE EXCEPTION 'outfit codes never change' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.scope <> OLD.scope OR NEW.owner_character_id IS DISTINCT FROM OLD.owner_character_id THEN
        RAISE EXCEPTION 'an outfit''s scope and owner never change; make a new outfit'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.recommended_version_id IS DISTINCT FROM OLD.recommended_version_id
       AND NEW.recommended_version_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM outfit_versions v JOIN asset_versions_registry r ON r.id = v.id
                       WHERE v.id = NEW.recommended_version_id AND v.outfit_id = NEW.id
                         AND r.status = 'published') THEN
        RAISE EXCEPTION 'the recommended version must be a published version of this outfit'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER outfits_guard
    BEFORE UPDATE ON outfits
    FOR EACH ROW EXECUTE FUNCTION studio_guard_outfit();

-- the registry is evidence: a pair never changes, an approval needs a verified fit, and an
-- approved fit's evidence is frozen; rows of a published outfit version are never deleted
CREATE FUNCTION studio_guard_outfit_compatibility() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    st text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT status INTO st FROM asset_versions_registry WHERE id = OLD.outfit_version_id;
        IF st IN ('published', 'deprecated') THEN
            RAISE EXCEPTION 'compatibility records of a % outfit version are kept', st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.id <> OLD.id OR NEW.outfit_version_id <> OLD.outfit_version_id
       OR NEW.character_version_id <> OLD.character_version_id THEN
        RAISE EXCEPTION 'a compatibility record''s pair never changes'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status = 'approved' AND OLD.status NOT IN ('verified', 'approved') THEN
        RAISE EXCEPTION 'only a verified fit can be approved (this one is %)', OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status = 'approved' AND (NEW.report IS DISTINCT FROM OLD.report OR NEW.files IS DISTINCT FROM OLD.files
       OR NEW.outfit_blend_sha256 IS DISTINCT FROM OLD.outfit_blend_sha256
       OR NEW.character_blend_sha256 IS DISTINCT FROM OLD.character_blend_sha256
       OR NEW.fit_job_id IS DISTINCT FROM OLD.fit_job_id) THEN
        RAISE EXCEPTION 'an approved fit''s evidence cannot change; revoke it first'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER outfit_compatibility_guard
    BEFORE UPDATE OR DELETE ON outfit_compatibility
    FOR EACH ROW EXECUTE FUNCTION studio_guard_outfit_compatibility();
"""

DROP_GUARDS = """
DROP TRIGGER IF EXISTS outfit_compatibility_guard ON outfit_compatibility;
DROP TRIGGER IF EXISTS outfits_guard ON outfits;
DROP TRIGGER IF EXISTS outfit_versions_guard ON outfit_versions;
DROP FUNCTION IF EXISTS studio_guard_outfit_compatibility();
DROP FUNCTION IF EXISTS studio_guard_outfit();
DROP FUNCTION IF EXISTS studio_guard_outfit_version();
"""


def upgrade() -> None:
    op.create_table('outfits',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False, comment='OUTFIT-001'),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('owner_character_id', sa.UUID(), nullable=True),
    sa.Column('recommended_version_id', sa.UUID(), nullable=True),
    sa.Column('lifecycle', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(scope = 'character') = (owner_character_id IS NOT NULL)", name=op.f('ck_outfits_owner_for_character_scope')),
    sa.CheckConstraint("scope IN ('character', 'body_profile', 'multi')", name=op.f('ck_outfits_scope')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_outfits_created_by_users')),
    sa.ForeignKeyConstraint(['owner_character_id'], ['characters.id'], name=op.f('fk_outfits_owner_character_id_characters')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outfits')),
    sa.UniqueConstraint('code', name=op.f('uq_outfits_code'))
    )
    op.create_index(op.f('ix_outfits_created_by'), 'outfits', ['created_by'], unique=False)
    op.create_index(op.f('ix_outfits_name'), 'outfits', ['name'], unique=False)
    op.create_index(op.f('ix_outfits_owner_character_id'), 'outfits', ['owner_character_id'], unique=False)
    op.create_table('outfit_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('outfit_id', sa.UUID(), nullable=False),
    sa.Column('based_on_version_id', sa.UUID(), nullable=True),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('spec_digest', sa.String(length=64), nullable=False, comment='ResolvedOutfit digest of spec'),
    sa.Column('built_digest', sa.String(length=64), nullable=True, comment='digest the current files were built from'),
    sa.Column('style_profile_ref', sa.String(length=64), nullable=True),
    sa.Column('rig_profile_ref', sa.String(length=64), nullable=True),
    sa.Column('catalog_ref', sa.String(length=64), nullable=True),
    sa.Column('fit_profiles', postgresql.ARRAY(sa.String(length=64)), nullable=False),
    sa.Column('primary_fit_profile', sa.String(length=64), nullable=True),
    sa.Column('slots', postgresql.ARRAY(sa.String(length=16)), nullable=False),
    sa.Column('fit_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='fit check on the reference body'),
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
    sa.ForeignKeyConstraint(['based_on_version_id'], ['outfit_versions.id'], name=op.f('fk_outfit_versions_based_on_version_id_outfit_versions')),
    sa.ForeignKeyConstraint(['generate_job_id'], ['jobs.id'], name=op.f('fk_outfit_versions_generate_job_id_jobs')),
    sa.ForeignKeyConstraint(['id'], ['asset_versions_registry.id'], name=op.f('fk_outfit_versions_id_asset_versions_registry'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['outfit_id'], ['outfits.id'], name=op.f('fk_outfit_versions_outfit_id_outfits')),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], name=op.f('fk_outfit_versions_published_by_users')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_outfit_versions_reviewed_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outfit_versions'))
    )
    op.create_index(op.f('ix_outfit_versions_outfit_id'), 'outfit_versions', ['outfit_id'], unique=False)
    op.create_table('outfit_compatibility',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('outfit_version_id', sa.UUID(), nullable=False),
    sa.Column('character_version_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('fit_job_id', sa.UUID(), nullable=True),
    sa.Column('fit_profile', sa.String(length=64), nullable=True, comment="the wearer's body-fit profile"),
    sa.Column('fit_profile_declared', sa.Boolean(), nullable=True, comment="the outfit declares the wearer's profile"),
    sa.Column('report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('files', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='report, thumbnail, pose sheet, try-on GLB'),
    sa.Column('outfit_blend_sha256', sa.String(length=64), nullable=True),
    sa.Column('character_blend_sha256', sa.String(length=64), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('candidate', 'verified', 'failed', 'approved', 'revoked')", name=op.f('ck_outfit_compatibility_status')),
    sa.ForeignKeyConstraint(['character_version_id'], ['character_versions.id'], name=op.f('fk_outfit_compatibility_character_version_id_character_versions')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_outfit_compatibility_decided_by_users')),
    sa.ForeignKeyConstraint(['fit_job_id'], ['jobs.id'], name=op.f('fk_outfit_compatibility_fit_job_id_jobs')),
    sa.ForeignKeyConstraint(['outfit_version_id'], ['outfit_versions.id'], name=op.f('fk_outfit_compatibility_outfit_version_id_outfit_versions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outfit_compatibility')),
    sa.UniqueConstraint('outfit_version_id', 'character_version_id', name=op.f('uq_outfit_compatibility_pair'))
    )
    op.create_index(op.f('ix_outfit_compatibility_character_version_id'), 'outfit_compatibility', ['character_version_id'], unique=False)
    op.create_index(op.f('ix_outfit_compatibility_outfit_version_id'), 'outfit_compatibility', ['outfit_version_id'], unique=False)
    op.create_foreign_key('fk_outfits_recommended_version_id', 'outfits', 'outfit_versions',
                          ['recommended_version_id'], ['id'])
    op.execute(GUARDS)


def downgrade() -> None:
    op.execute(DROP_GUARDS)
    op.drop_constraint('fk_outfits_recommended_version_id', 'outfits', type_='foreignkey')
    op.drop_index(op.f('ix_outfit_compatibility_outfit_version_id'), table_name='outfit_compatibility')
    op.drop_index(op.f('ix_outfit_compatibility_character_version_id'), table_name='outfit_compatibility')
    op.drop_table('outfit_compatibility')
    op.drop_index(op.f('ix_outfit_versions_outfit_id'), table_name='outfit_versions')
    op.drop_table('outfit_versions')
    op.drop_index(op.f('ix_outfits_owner_character_id'), table_name='outfits')
    op.drop_index(op.f('ix_outfits_name'), table_name='outfits')
    op.drop_index(op.f('ix_outfits_created_by'), table_name='outfits')
    op.drop_table('outfits')
