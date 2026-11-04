"""character identity and versions, with their immutability guards (architecture 6.2)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


GUARDS = r"""
CREATE FUNCTION studio_guard_character_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    st text;
    probe character_versions;
BEGIN
    SELECT status INTO st FROM asset_versions_registry WHERE id = OLD.id;
    IF TG_OP = 'DELETE' THEN
        IF st IN ('published', 'deprecated') THEN
            RAISE EXCEPTION 'character version % is %, it cannot be deleted', OLD.id, st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.id <> OLD.id OR NEW.character_id <> OLD.character_id
       OR NEW.based_on_version_id IS DISTINCT FROM OLD.based_on_version_id THEN
        RAISE EXCEPTION 'character version identity columns are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    probe := NEW;
    probe.updated_at := OLD.updated_at;
    IF st IN ('published', 'deprecated') THEN
        probe.deprecated_at := OLD.deprecated_at;
        IF probe IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'character version % is %, it cannot be changed', OLD.id, st
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF st = 'review' THEN
        -- the build is frozen for review; only the review and publish record may change
        probe.review_decision := OLD.review_decision; probe.review_notes := OLD.review_notes;
        probe.reviewed_by := OLD.reviewed_by; probe.reviewed_at := OLD.reviewed_at;
        probe.manifest_key := OLD.manifest_key; probe.manifest_sha256 := OLD.manifest_sha256;
        probe.published_by := OLD.published_by; probe.published_at := OLD.published_at;
        IF probe IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'character version % is in review, its build cannot change', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER character_versions_guard
    BEFORE UPDATE OR DELETE ON character_versions
    FOR EACH ROW EXECUTE FUNCTION studio_guard_character_version();

CREATE FUNCTION studio_guard_character() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.code <> OLD.code OR NEW.id <> OLD.id THEN
        RAISE EXCEPTION 'character codes never change' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.recommended_version_id IS DISTINCT FROM OLD.recommended_version_id
       AND NEW.recommended_version_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM character_versions v JOIN asset_versions_registry r ON r.id = v.id
                       WHERE v.id = NEW.recommended_version_id AND v.character_id = NEW.id
                         AND r.status = 'published') THEN
        RAISE EXCEPTION 'the recommended version must be a published version of this character'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER characters_guard
    BEFORE UPDATE ON characters
    FOR EACH ROW EXECUTE FUNCTION studio_guard_character();
"""

DROP_GUARDS = """
DROP TRIGGER IF EXISTS characters_guard ON characters;
DROP TRIGGER IF EXISTS character_versions_guard ON character_versions;
DROP FUNCTION IF EXISTS studio_guard_character();
DROP FUNCTION IF EXISTS studio_guard_character_version();
"""


def upgrade() -> None:
    op.create_table('characters',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False, comment='CHAR-001'),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', sa.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('recommended_version_id', sa.UUID(), nullable=True),
    sa.Column('lifecycle', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_characters_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_characters')),
    sa.UniqueConstraint('code', name=op.f('uq_characters_code'))
    )
    op.create_index(op.f('ix_characters_created_by'), 'characters', ['created_by'], unique=False)
    op.create_index(op.f('ix_characters_name'), 'characters', ['name'], unique=False)
    op.create_table('character_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('based_on_version_id', sa.UUID(), nullable=True),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('spec_digest', sa.String(length=64), nullable=False, comment='ResolvedCharacter digest of spec'),
    sa.Column('built_digest', sa.String(length=64), nullable=True, comment='digest the current files were built from'),
    sa.Column('template_ref', sa.String(length=64), nullable=True),
    sa.Column('style_profile_ref', sa.String(length=64), nullable=True),
    sa.Column('rig_profile_ref', sa.String(length=64), nullable=True),
    sa.Column('fit_profile', sa.String(length=64), nullable=True),
    sa.Column('height_m', sa.Float(), nullable=True),
    sa.Column('validation_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
    sa.ForeignKeyConstraint(['based_on_version_id'], ['character_versions.id'], name=op.f('fk_character_versions_based_on_version_id_character_versions')),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], name=op.f('fk_character_versions_character_id_characters')),
    sa.ForeignKeyConstraint(['generate_job_id'], ['jobs.id'], name=op.f('fk_character_versions_generate_job_id_jobs')),
    sa.ForeignKeyConstraint(['id'], ['asset_versions_registry.id'], name=op.f('fk_character_versions_id_asset_versions_registry'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], name=op.f('fk_character_versions_published_by_users')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_character_versions_reviewed_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_character_versions'))
    )
    op.create_index(op.f('ix_character_versions_character_id'), 'character_versions', ['character_id'], unique=False)
    op.create_foreign_key('fk_characters_recommended_version_id', 'characters', 'character_versions',
                          ['recommended_version_id'], ['id'])
    op.execute(GUARDS)


def downgrade() -> None:
    op.execute(DROP_GUARDS)
    op.drop_constraint('fk_characters_recommended_version_id', 'characters', type_='foreignkey')
    op.drop_index(op.f('ix_character_versions_character_id'), table_name='character_versions')
    op.drop_table('character_versions')
    op.drop_index(op.f('ix_characters_name'), table_name='characters')
    op.drop_index(op.f('ix_characters_created_by'), table_name='characters')
    op.drop_table('characters')
