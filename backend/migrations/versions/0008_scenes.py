"""scenes, scene versions and their pinned instances, with guards; scene.edit permission (SRS 16)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


GUARDS = r"""
CREATE FUNCTION studio_guard_scene() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'scenes are never deleted; archive them' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.code <> OLD.code OR NEW.id <> OLD.id THEN
        RAISE EXCEPTION 'scene codes never change' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER scenes_guard BEFORE UPDATE OR DELETE ON scenes
    FOR EACH ROW EXECUTE FUNCTION studio_guard_scene();

-- A scene version is append-only: what it pins never changes. Its build fills once
-- (queued -> built | failed, and a failed build may be queued again); a built version's
-- build columns are frozen. Reopen checks may be run again on a built version.
CREATE FUNCTION studio_guard_scene_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    probe scene_versions;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'scene version % cannot be deleted', OLD.id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    probe := OLD;
    probe.build_status := NEW.build_status; probe.assemble_job_id := NEW.assemble_job_id;
    probe.scene_blend_sha256 := NEW.scene_blend_sha256; probe.build_report := NEW.build_report;
    probe.build_files := NEW.build_files; probe.blender_version := NEW.blender_version;
    probe.built_at := NEW.built_at;
    probe.verify_status := NEW.verify_status; probe.verify_job_id := NEW.verify_job_id;
    probe.verify_report := NEW.verify_report; probe.verify_files := NEW.verify_files;
    probe.verified_at := NEW.verified_at; probe.updated_at := NEW.updated_at;
    IF probe IS DISTINCT FROM NEW THEN
        RAISE EXCEPTION 'scene version % is immutable: its manifest and pins never change', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.build_status = 'built' AND (
           NEW.build_status IS DISTINCT FROM OLD.build_status OR NEW.assemble_job_id IS DISTINCT FROM OLD.assemble_job_id
        OR NEW.scene_blend_sha256 IS DISTINCT FROM OLD.scene_blend_sha256
        OR NEW.build_report IS DISTINCT FROM OLD.build_report OR NEW.build_files IS DISTINCT FROM OLD.build_files
        OR NEW.blender_version IS DISTINCT FROM OLD.blender_version OR NEW.built_at IS DISTINCT FROM OLD.built_at) THEN
        RAISE EXCEPTION 'scene version % is built; its scene.blend never changes (make a new version)', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER scene_versions_guard BEFORE UPDATE OR DELETE ON scene_versions
    FOR EACH ROW EXECUTE FUNCTION studio_guard_scene_version();

CREATE FUNCTION studio_guard_scene_pins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the pins of a scene version never change' USING ERRCODE = 'integrity_constraint_violation';
END $$;

CREATE TRIGGER scene_instances_guard BEFORE UPDATE OR DELETE ON scene_instances
    FOR EACH ROW EXECUTE FUNCTION studio_guard_scene_pins();
CREATE TRIGGER scene_instance_accessories_guard BEFORE UPDATE OR DELETE ON scene_instance_accessories
    FOR EACH ROW EXECUTE FUNCTION studio_guard_scene_pins();
"""

DROP_GUARDS = """
DROP TRIGGER IF EXISTS scene_instance_accessories_guard ON scene_instance_accessories;
DROP TRIGGER IF EXISTS scene_instances_guard ON scene_instances;
DROP TRIGGER IF EXISTS scene_versions_guard ON scene_versions;
DROP TRIGGER IF EXISTS scenes_guard ON scenes;
DROP FUNCTION IF EXISTS studio_guard_scene_pins();
DROP FUNCTION IF EXISTS studio_guard_scene_version();
DROP FUNCTION IF EXISTS studio_guard_scene();
"""

# SRS 3: building scenes. Kept in sync with app.core.permissions by tests/test_permissions.py.
PERMISSION = ("scene.edit", "Create scenes and scene versions, assemble and reopen them")
GRANTED_TO = ("admin", "creator", "director")


def _perm_id() -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"studio:permission:{PERMISSION[0]}")


def upgrade() -> None:
    op.create_table('scenes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False, comment='SCENE-001'),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('lifecycle', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_scenes_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scenes')),
    sa.UniqueConstraint('code', name=op.f('uq_scenes_code'))
    )
    op.create_index(op.f('ix_scenes_created_by'), 'scenes', ['created_by'], unique=False)
    op.create_index(op.f('ix_scenes_name'), 'scenes', ['name'], unique=False)
    op.create_table('scene_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('scene_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('based_on_version_id', sa.UUID(), nullable=True),
    sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='SceneManifest as written'),
    sa.Column('manifest_digest', sa.String(length=64), nullable=False),
    sa.Column('resolved', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='ResolvedScene: exact version ids and SHA-256s'),
    sa.Column('overrides', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='verified-but-unapproved fits a director accepted for this version'),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('build_status', sa.String(length=16), nullable=False),
    sa.Column('assemble_job_id', sa.UUID(), nullable=True),
    sa.Column('scene_blend_sha256', sa.String(length=64), nullable=True),
    sa.Column('build_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('build_files', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='scene.blend, report, frames, thumbnail'),
    sa.Column('blender_version', sa.String(length=32), nullable=True),
    sa.Column('built_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verify_status', sa.String(length=16), nullable=False),
    sa.Column('verify_job_id', sa.UUID(), nullable=True),
    sa.Column('verify_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('verify_files', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='reopen report, reopened frames'),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("build_status IN ('none', 'queued', 'built', 'failed')", name=op.f('ck_scene_versions_build_status')),
    sa.CheckConstraint("verify_status = 'none' OR build_status = 'built'", name=op.f('ck_scene_versions_verify_needs_build')),
    sa.CheckConstraint("verify_status IN ('none', 'queued', 'verified', 'failed')", name=op.f('ck_scene_versions_verify_status')),
    sa.CheckConstraint('number >= 1', name=op.f('ck_scene_versions_number')),
    sa.ForeignKeyConstraint(['assemble_job_id'], ['jobs.id'], name=op.f('fk_scene_versions_assemble_job_id_jobs')),
    sa.ForeignKeyConstraint(['based_on_version_id'], ['scene_versions.id'], name=op.f('fk_scene_versions_based_on_version_id_scene_versions')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_scene_versions_created_by_users')),
    sa.ForeignKeyConstraint(['scene_id'], ['scenes.id'], name=op.f('fk_scene_versions_scene_id_scenes')),
    sa.ForeignKeyConstraint(['verify_job_id'], ['jobs.id'], name=op.f('fk_scene_versions_verify_job_id_jobs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scene_versions')),
    sa.UniqueConstraint('scene_id', 'number', name='uq_scene_versions_number')
    )
    op.create_index(op.f('ix_scene_versions_scene_id'), 'scene_versions', ['scene_id'], unique=False)
    op.create_table('scene_instances',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('scene_version_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('character_version_id', sa.UUID(), nullable=False),
    sa.Column('outfit_version_id', sa.UUID(), nullable=True),
    sa.Column('outfit_compatibility_id', sa.UUID(), nullable=True),
    sa.Column('compatibility_status', sa.String(length=16), nullable=True, comment="the fit's status when pinned"),
    sa.Column('clips', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.ForeignKeyConstraint(['character_version_id'], ['character_versions.id'], name=op.f('fk_scene_instances_character_version_id_character_versions')),
    sa.ForeignKeyConstraint(['outfit_compatibility_id'], ['outfit_compatibility.id'], name=op.f('fk_scene_instances_outfit_compatibility_id_outfit_compatibility')),
    sa.ForeignKeyConstraint(['outfit_version_id'], ['outfit_versions.id'], name=op.f('fk_scene_instances_outfit_version_id_outfit_versions')),
    sa.ForeignKeyConstraint(['scene_version_id'], ['scene_versions.id'], name=op.f('fk_scene_instances_scene_version_id_scene_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scene_instances')),
    sa.UniqueConstraint('scene_version_id', 'key', name='uq_scene_instances_key')
    )
    op.create_index(op.f('ix_scene_instances_character_version_id'), 'scene_instances', ['character_version_id'], unique=False)
    op.create_index(op.f('ix_scene_instances_outfit_version_id'), 'scene_instances', ['outfit_version_id'], unique=False)
    op.create_index(op.f('ix_scene_instances_scene_version_id'), 'scene_instances', ['scene_version_id'], unique=False)
    op.create_table('scene_instance_accessories',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('instance_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('accessory_version_id', sa.UUID(), nullable=False),
    sa.Column('attach_point', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['accessory_version_id'], ['accessory_versions.id'], name=op.f('fk_scene_instance_accessories_accessory_version_id_accessory_versions')),
    sa.ForeignKeyConstraint(['instance_id'], ['scene_instances.id'], name=op.f('fk_scene_instance_accessories_instance_id_scene_instances')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scene_instance_accessories')),
    sa.UniqueConstraint('instance_id', 'attach_point', name='uq_scene_instance_accessories_point')
    )
    op.create_index(op.f('ix_scene_instance_accessories_accessory_version_id'), 'scene_instance_accessories', ['accessory_version_id'], unique=False)
    op.create_index(op.f('ix_scene_instance_accessories_instance_id'), 'scene_instance_accessories', ['instance_id'], unique=False)
    op.execute(GUARDS)
    perms = sa.table("permissions", sa.column("id", sa.UUID), sa.column("name", sa.String),
                     sa.column("description", sa.Text))
    links = sa.table("role_permissions", sa.column("role_id", sa.UUID), sa.column("permission_id", sa.UUID))
    op.bulk_insert(perms, [{"id": _perm_id(), "name": PERMISSION[0], "description": PERMISSION[1]}])
    op.bulk_insert(links, [{"role_id": uuid.uuid5(uuid.NAMESPACE_URL, f"studio:role:{r}"), "permission_id": _perm_id()}
                           for r in GRANTED_TO])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p").bindparams(p=_perm_id()))
    op.execute(sa.text("DELETE FROM permissions WHERE id = :p").bindparams(p=_perm_id()))
    op.execute(DROP_GUARDS)
    op.drop_index(op.f('ix_scene_instance_accessories_instance_id'), table_name='scene_instance_accessories')
    op.drop_index(op.f('ix_scene_instance_accessories_accessory_version_id'), table_name='scene_instance_accessories')
    op.drop_table('scene_instance_accessories')
    op.drop_index(op.f('ix_scene_instances_scene_version_id'), table_name='scene_instances')
    op.drop_index(op.f('ix_scene_instances_outfit_version_id'), table_name='scene_instances')
    op.drop_index(op.f('ix_scene_instances_character_version_id'), table_name='scene_instances')
    op.drop_table('scene_instances')
    op.drop_index(op.f('ix_scene_versions_scene_id'), table_name='scene_versions')
    op.drop_table('scene_versions')
    op.drop_index(op.f('ix_scenes_name'), table_name='scenes')
    op.drop_index(op.f('ix_scenes_created_by'), table_name='scenes')
    op.drop_table('scenes')
