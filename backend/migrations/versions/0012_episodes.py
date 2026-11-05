"""series, episodes, scripts, storyboards, episode versions and render jobs (SRS 30 Phase 8)

Guards:
* script versions, storyboards, shots and render outputs are append-only;
* an episode version's number and episode never change; once locked nothing about it
  changes, it is never deleted, and its shots, asset closure and audio are frozen with it;
* a render job is only made for a locked episode version, and the version, settings and
  asset versions it records never change;
* audio clips gain music / sound effects and speech-synthesised dialogue; the clip guard
  now also keeps the kind, source, voice and provider.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-05 09:34:54.079923
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

APPEND_ONLY = ("script_versions", "storyboards", "shots", "render_outputs")

GUARDS = r"""
CREATE FUNCTION studio_guard_episode_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'locked' THEN
            RAISE EXCEPTION 'episode version % is locked, it cannot be deleted', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'locked' THEN
        RAISE EXCEPTION 'episode version % is locked, it never changes', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.id <> OLD.id OR NEW.episode_id <> OLD.episode_id OR NEW.number <> OLD.number THEN
        RAISE EXCEPTION 'episode version identity columns are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER episode_versions_guard BEFORE UPDATE OR DELETE ON episode_versions
    FOR EACH ROW EXECUTE FUNCTION studio_guard_episode_version();

CREATE FUNCTION studio_guard_episode_version_part() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    vid uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN vid := OLD.episode_version_id; ELSE vid := NEW.episode_version_id; END IF;
    IF EXISTS (SELECT 1 FROM episode_versions WHERE id = vid AND status = 'locked')
       OR (TG_OP = 'UPDATE' AND EXISTS (SELECT 1 FROM episode_versions
                                         WHERE id = OLD.episode_version_id AND status = 'locked')) THEN
        RAISE EXCEPTION 'episode version % is locked, its % are frozen', vid, TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER episode_version_shots_guard BEFORE INSERT OR UPDATE OR DELETE ON episode_version_shots
    FOR EACH ROW EXECUTE FUNCTION studio_guard_episode_version_part();
CREATE TRIGGER episode_assets_guard BEFORE INSERT OR UPDATE OR DELETE ON episode_assets
    FOR EACH ROW EXECUTE FUNCTION studio_guard_episode_version_part();
CREATE TRIGGER episode_audio_guard BEFORE INSERT OR UPDATE OR DELETE ON episode_audio
    FOR EACH ROW EXECUTE FUNCTION studio_guard_episode_version_part();

CREATE FUNCTION studio_guard_render_job() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'render job % is kept as a record, it cannot be deleted', OLD.code
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM episode_versions WHERE id = NEW.episode_version_id
                         AND episode_id = NEW.episode_id AND status = 'locked') THEN
            RAISE EXCEPTION 'only a locked version of this episode can be rendered'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id <> OLD.id OR NEW.code <> OLD.code OR NEW.episode_id <> OLD.episode_id
       OR NEW.episode_version_id <> OLD.episode_version_id OR NEW.settings <> OLD.settings
       OR NEW.asset_versions <> OLD.asset_versions OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'render job % keeps its episode version, settings and asset versions', OLD.code
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status IN ('completed', 'failed', 'cancelled') AND NEW IS DISTINCT FROM OLD
       AND NOT (OLD.status = 'failed' AND NEW.status IN ('queued', 'rendering', 'exporting')) THEN
        RAISE EXCEPTION 'render job % is %, it no longer changes', OLD.code, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER render_jobs_guard BEFORE INSERT OR UPDATE OR DELETE ON render_jobs
    FOR EACH ROW EXECUTE FUNCTION studio_guard_render_job();

CREATE FUNCTION studio_guard_render_job_shot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR NEW.render_job_id <> OLD.render_job_id OR NEW.position <> OLD.position
       OR NEW.shot_id <> OLD.shot_id OR NEW.scene_version_id <> OLD.scene_version_id THEN
        RAISE EXCEPTION 'a render job''s shots are fixed when it is made'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER render_job_shots_guard BEFORE UPDATE OR DELETE ON render_job_shots
    FOR EACH ROW EXECUTE FUNCTION studio_guard_render_job_shot();
""" + "".join(f"""
CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t}
    FOR EACH ROW EXECUTE FUNCTION studio_append_only();
""" for t in APPEND_ONLY)

DROP_GUARDS = "".join(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t};\n" for t in APPEND_ONLY) + """
DROP TRIGGER IF EXISTS render_job_shots_guard ON render_job_shots;
DROP TRIGGER IF EXISTS render_jobs_guard ON render_jobs;
DROP TRIGGER IF EXISTS episode_audio_guard ON episode_audio;
DROP TRIGGER IF EXISTS episode_assets_guard ON episode_assets;
DROP TRIGGER IF EXISTS episode_version_shots_guard ON episode_version_shots;
DROP TRIGGER IF EXISTS episode_versions_guard ON episode_versions;
DROP FUNCTION IF EXISTS studio_guard_render_job_shot();
DROP FUNCTION IF EXISTS studio_guard_render_job();
DROP FUNCTION IF EXISTS studio_guard_episode_version_part();
DROP FUNCTION IF EXISTS studio_guard_episode_version();
"""

AUDIO_GUARD = r"""
CREATE OR REPLACE FUNCTION studio_guard_audio_clip() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'ready' THEN
            RAISE EXCEPTION 'audio clip % is ready, it cannot be deleted', OLD.code
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'analysing' THEN
        RAISE EXCEPTION 'audio clip % is %, it never changes', OLD.code, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.id <> OLD.id OR NEW.code <> OLD.code OR NEW.upload_id IS DISTINCT FROM OLD.upload_id
       OR NEW.kind <> OLD.kind OR NEW.source <> OLD.source OR NEW.voice IS DISTINCT FROM OLD.voice
       OR NEW.dialog_text IS DISTINCT FROM OLD.dialog_text OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION 'audio clip % keeps its identity, input, voice and transcript', OLD.code
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""

AUDIO_GUARD_0010 = r"""
CREATE OR REPLACE FUNCTION studio_guard_audio_clip() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'ready' THEN
            RAISE EXCEPTION 'audio clip % is ready, it cannot be deleted', OLD.code
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'analysing' THEN
        RAISE EXCEPTION 'audio clip % is %, it never changes', OLD.code, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.id <> OLD.id OR NEW.code <> OLD.code OR NEW.upload_id <> OLD.upload_id
       OR NEW.dialog_text IS DISTINCT FROM OLD.dialog_text OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION 'audio clip % keeps its identity, upload and transcript', OLD.code
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('series',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False, comment='SERIES-001'),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('style_profile_ref', sa.String(length=64), nullable=False, comment='family_cartoon@1.0'),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='SeriesSettings'),
    sa.Column('lifecycle', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_series_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_series')),
    sa.UniqueConstraint('code', name=op.f('uq_series_code'))
    )
    op.create_index(op.f('ix_series_created_by'), 'series', ['created_by'], unique=False)
    op.create_index(op.f('ix_series_name'), 'series', ['name'], unique=False)
    op.create_table('episodes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False, comment='EP-001'),
    sa.Column('series_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('concept', sa.Text(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_episodes_created_by_users')),
    sa.ForeignKeyConstraint(['series_id'], ['series.id'], name=op.f('fk_episodes_series_id_series')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_episodes')),
    sa.UniqueConstraint('code', name=op.f('uq_episodes_code')),
    sa.UniqueConstraint('series_id', 'number', name='uq_episodes_number')
    )
    op.create_index(op.f('ix_episodes_created_by'), 'episodes', ['created_by'], unique=False)
    op.create_index(op.f('ix_episodes_series_id'), 'episodes', ['series_id'], unique=False)
    op.create_table('series_assets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('series_id', sa.UUID(), nullable=False),
    sa.Column('asset_version_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['asset_version_id'], ['asset_versions_registry.id'], name=op.f('fk_series_assets_asset_version_id_asset_versions_registry')),
    sa.ForeignKeyConstraint(['series_id'], ['series.id'], name=op.f('fk_series_assets_series_id_series')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_series_assets')),
    sa.UniqueConstraint('series_id', 'asset_version_id', name='uq_series_assets_version')
    )
    op.create_index(op.f('ix_series_assets_asset_version_id'), 'series_assets', ['asset_version_id'], unique=False)
    op.create_index(op.f('ix_series_assets_series_id'), 'series_assets', ['series_id'], unique=False)
    op.create_table('episode_scenes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('shot_id', sa.String(length=16), nullable=False),
    sa.Column('scene_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], name=op.f('fk_episode_scenes_episode_id_episodes')),
    sa.ForeignKeyConstraint(['scene_id'], ['scenes.id'], name=op.f('fk_episode_scenes_scene_id_scenes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_episode_scenes')),
    sa.UniqueConstraint('episode_id', 'shot_id', name='uq_episode_scenes_shot'),
    sa.UniqueConstraint('scene_id', name=op.f('uq_episode_scenes_scene_id'))
    )
    op.create_index(op.f('ix_episode_scenes_episode_id'), 'episode_scenes', ['episode_id'], unique=False)
    op.create_table('scripts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], name=op.f('fk_scripts_episode_id_episodes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scripts')),
    sa.UniqueConstraint('episode_id', name=op.f('uq_scripts_episode_id'))
    )
    op.create_table('script_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('script_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='ScriptSpecification'),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('ai_generation_id', sa.UUID(), nullable=True),
    sa.Column('based_on_id', sa.UUID(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source <> 'ai' OR ai_generation_id IS NOT NULL", name=op.f('ck_script_versions_ai_logged')),
    sa.CheckConstraint("source IN ('manual', 'ai')", name=op.f('ck_script_versions_source')),
    sa.ForeignKeyConstraint(['ai_generation_id'], ['ai_generations.id'], name=op.f('fk_script_versions_ai_generation_id_ai_generations')),
    sa.ForeignKeyConstraint(['based_on_id'], ['script_versions.id'], name=op.f('fk_script_versions_based_on_id_script_versions')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_script_versions_created_by_users')),
    sa.ForeignKeyConstraint(['script_id'], ['scripts.id'], name=op.f('fk_script_versions_script_id_scripts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_script_versions')),
    sa.UniqueConstraint('script_id', 'number', name='uq_script_versions_number')
    )
    op.create_index(op.f('ix_script_versions_script_id'), 'script_versions', ['script_id'], unique=False)
    op.create_table('series_members',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('series_id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=32), nullable=False, comment="instance key in the series' scenes, e.g. ali"),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('default_character_version_id', sa.UUID(), nullable=True),
    sa.Column('default_outfit_version_id', sa.UUID(), nullable=True),
    sa.Column('voice', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='VoiceProfile: the same voice in every episode'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('main', 'supporting')", name=op.f('ck_series_members_role')),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], name=op.f('fk_series_members_character_id_characters')),
    sa.ForeignKeyConstraint(['default_character_version_id'], ['character_versions.id'], name=op.f('fk_series_members_default_character_version_id_character_versions')),
    sa.ForeignKeyConstraint(['default_outfit_version_id'], ['outfit_versions.id'], name=op.f('fk_series_members_default_outfit_version_id_outfit_versions')),
    sa.ForeignKeyConstraint(['series_id'], ['series.id'], name=op.f('fk_series_members_series_id_series')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_series_members')),
    sa.UniqueConstraint('series_id', 'character_id', name='uq_series_members_character'),
    sa.UniqueConstraint('series_id', 'key', name='uq_series_members_key')
    )
    op.create_index(op.f('ix_series_members_character_id'), 'series_members', ['character_id'], unique=False)
    op.create_index(op.f('ix_series_members_series_id'), 'series_members', ['series_id'], unique=False)
    op.create_table('storyboards',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('script_version_id', sa.UUID(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('ai_generation_id', sa.UUID(), nullable=True),
    sa.Column('based_on_id', sa.UUID(), nullable=True),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('duration_s', sa.Float(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source <> 'ai' OR ai_generation_id IS NOT NULL", name=op.f('ck_storyboards_ai_logged')),
    sa.CheckConstraint("source IN ('rule', 'ai', 'manual', 'voiced')", name=op.f('ck_storyboards_source')),
    sa.ForeignKeyConstraint(['ai_generation_id'], ['ai_generations.id'], name=op.f('fk_storyboards_ai_generation_id_ai_generations')),
    sa.ForeignKeyConstraint(['based_on_id'], ['storyboards.id'], name=op.f('fk_storyboards_based_on_id_storyboards')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_storyboards_created_by_users')),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], name=op.f('fk_storyboards_episode_id_episodes')),
    sa.ForeignKeyConstraint(['script_version_id'], ['script_versions.id'], name=op.f('fk_storyboards_script_version_id_script_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_storyboards')),
    sa.UniqueConstraint('episode_id', 'number', name='uq_storyboards_number')
    )
    op.create_index(op.f('ix_storyboards_episode_id'), 'storyboards', ['episode_id'], unique=False)
    op.create_index(op.f('ix_storyboards_script_version_id'), 'storyboards', ['script_version_id'], unique=False)
    op.create_table('episode_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='EpisodeManifest'),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('script_version_id', sa.UUID(), nullable=False),
    sa.Column('storyboard_id', sa.UUID(), nullable=False),
    sa.Column('duration_s', sa.Float(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('locked_by', sa.UUID(), nullable=True),
    sa.CheckConstraint("status <> 'locked' OR locked_at IS NOT NULL", name=op.f('ck_episode_versions_locked_at')),
    sa.CheckConstraint("status IN ('draft', 'locked')", name=op.f('ck_episode_versions_status')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_episode_versions_created_by_users')),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], name=op.f('fk_episode_versions_episode_id_episodes')),
    sa.ForeignKeyConstraint(['locked_by'], ['users.id'], name=op.f('fk_episode_versions_locked_by_users')),
    sa.ForeignKeyConstraint(['script_version_id'], ['script_versions.id'], name=op.f('fk_episode_versions_script_version_id_script_versions')),
    sa.ForeignKeyConstraint(['storyboard_id'], ['storyboards.id'], name=op.f('fk_episode_versions_storyboard_id_storyboards')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_episode_versions')),
    sa.UniqueConstraint('episode_id', 'number', name='uq_episode_versions_number')
    )
    op.create_index(op.f('ix_episode_versions_episode_id'), 'episode_versions', ['episode_id'], unique=False)
    op.create_table('shots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('storyboard_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('shot_id', sa.String(length=16), nullable=False, comment='SH-001'),
    sa.Column('scene_id', sa.String(length=16), nullable=False, comment='the script scene, SC-001'),
    sa.Column('duration_s', sa.Float(), nullable=False),
    sa.Column('camera_type', sa.String(length=16), nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Shot'),
    sa.ForeignKeyConstraint(['storyboard_id'], ['storyboards.id'], name=op.f('fk_shots_storyboard_id_storyboards')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shots')),
    sa.UniqueConstraint('storyboard_id', 'position', name='uq_shots_position'),
    sa.UniqueConstraint('storyboard_id', 'shot_id', name='uq_shots_shot_id')
    )
    op.create_index(op.f('ix_shots_storyboard_id'), 'shots', ['storyboard_id'], unique=False)
    op.create_table('episode_assets',
    sa.Column('episode_version_id', sa.UUID(), nullable=False),
    sa.Column('asset_version_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('display', sa.String(length=48), nullable=False, comment='CHAR-001@1.0'),
    sa.ForeignKeyConstraint(['asset_version_id'], ['asset_versions_registry.id'], name=op.f('fk_episode_assets_asset_version_id_asset_versions_registry')),
    sa.ForeignKeyConstraint(['episode_version_id'], ['episode_versions.id'], name=op.f('fk_episode_assets_episode_version_id_episode_versions')),
    sa.PrimaryKeyConstraint('episode_version_id', 'asset_version_id', name=op.f('pk_episode_assets'))
    )
    op.create_index(op.f('ix_episode_assets_asset_version_id'), 'episode_assets', ['asset_version_id'], unique=False)
    op.create_table('episode_audio',
    sa.Column('episode_version_id', sa.UUID(), nullable=False),
    sa.Column('audio_clip_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False, comment='dialogue | music | sfx'),
    sa.ForeignKeyConstraint(['audio_clip_id'], ['audio_clips.id'], name=op.f('fk_episode_audio_audio_clip_id_audio_clips')),
    sa.ForeignKeyConstraint(['episode_version_id'], ['episode_versions.id'], name=op.f('fk_episode_audio_episode_version_id_episode_versions')),
    sa.PrimaryKeyConstraint('episode_version_id', 'audio_clip_id', 'role', name=op.f('pk_episode_audio'))
    )
    op.create_index(op.f('ix_episode_audio_audio_clip_id'), 'episode_audio', ['audio_clip_id'], unique=False)
    op.create_table('episode_version_shots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('episode_version_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('shot_id', sa.String(length=16), nullable=False),
    sa.Column('scene_version_id', sa.UUID(), nullable=False),
    sa.Column('start_s', sa.Float(), nullable=False, comment='where the shot starts on the episode timeline'),
    sa.Column('duration_s', sa.Float(), nullable=False),
    sa.Column('transition', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Transition in'),
    sa.ForeignKeyConstraint(['episode_version_id'], ['episode_versions.id'], name=op.f('fk_episode_version_shots_episode_version_id_episode_versions')),
    sa.ForeignKeyConstraint(['scene_version_id'], ['scene_versions.id'], name=op.f('fk_episode_version_shots_scene_version_id_scene_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_episode_version_shots')),
    sa.UniqueConstraint('episode_version_id', 'position', name='uq_episode_version_shots_position'),
    sa.UniqueConstraint('episode_version_id', 'shot_id', name='uq_episode_version_shots_shot')
    )
    op.create_index(op.f('ix_episode_version_shots_episode_version_id'), 'episode_version_shots', ['episode_version_id'], unique=False)
    op.create_index(op.f('ix_episode_version_shots_scene_version_id'), 'episode_version_shots', ['scene_version_id'], unique=False)
    op.create_table('render_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False, comment='RENDER-001'),
    sa.Column('episode_id', sa.UUID(), nullable=False),
    sa.Column('episode_version_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='RenderSettings in force'),
    sa.Column('asset_versions', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='the closure, CHAR-001@1.0 …, as rendered'),
    sa.Column('blender_version', sa.String(length=32), nullable=True),
    sa.Column('export_job_id', sa.UUID(), nullable=True),
    sa.Column('output_location', sa.Text(), nullable=True, comment='work bucket key of the MP4'),
    sa.Column('error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('requested_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('queued', 'rendering', 'exporting', 'completed', 'failed', 'cancelled')", name=op.f('ck_render_jobs_status')),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], name=op.f('fk_render_jobs_episode_id_episodes')),
    sa.ForeignKeyConstraint(['episode_version_id'], ['episode_versions.id'], name=op.f('fk_render_jobs_episode_version_id_episode_versions')),
    sa.ForeignKeyConstraint(['export_job_id'], ['jobs.id'], name=op.f('fk_render_jobs_export_job_id_jobs')),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_render_jobs_requested_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_render_jobs')),
    sa.UniqueConstraint('code', name=op.f('uq_render_jobs_code'))
    )
    op.create_index(op.f('ix_render_jobs_episode_id'), 'render_jobs', ['episode_id'], unique=False)
    op.create_index(op.f('ix_render_jobs_episode_version_id'), 'render_jobs', ['episode_version_id'], unique=False)
    op.create_index(op.f('ix_render_jobs_status'), 'render_jobs', ['status'], unique=False)
    op.create_table('render_job_shots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('render_job_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('shot_id', sa.String(length=16), nullable=False),
    sa.Column('scene_version_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('frames', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_render_job_shots_job_id_jobs')),
    sa.ForeignKeyConstraint(['render_job_id'], ['render_jobs.id'], name=op.f('fk_render_job_shots_render_job_id_render_jobs')),
    sa.ForeignKeyConstraint(['scene_version_id'], ['scene_versions.id'], name=op.f('fk_render_job_shots_scene_version_id_scene_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_render_job_shots')),
    sa.UniqueConstraint('render_job_id', 'position', name='uq_render_job_shots_position')
    )
    op.create_index(op.f('ix_render_job_shots_job_id'), 'render_job_shots', ['job_id'], unique=False)
    op.create_index(op.f('ix_render_job_shots_render_job_id'), 'render_job_shots', ['render_job_id'], unique=False)
    op.create_index(op.f('ix_render_job_shots_scene_version_id'), 'render_job_shots', ['scene_version_id'], unique=False)
    op.create_table('render_outputs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('render_job_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False, comment='frames | video | audio | report'),
    sa.Column('scene_version_id', sa.UUID(), nullable=True),
    sa.Column('shot_id', sa.String(length=16), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False, comment="the object, or the frames' common prefix"),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('frame_count', sa.Integer(), nullable=True),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='frames with SHA-256, ffprobe, …'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_render_outputs_job_id_jobs')),
    sa.ForeignKeyConstraint(['render_job_id'], ['render_jobs.id'], name=op.f('fk_render_outputs_render_job_id_render_jobs')),
    sa.ForeignKeyConstraint(['scene_version_id'], ['scene_versions.id'], name=op.f('fk_render_outputs_scene_version_id_scene_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_render_outputs'))
    )
    op.create_index(op.f('ix_render_outputs_render_job_id'), 'render_outputs', ['render_job_id'], unique=False)
    op.add_column('audio_clips', sa.Column('kind', sa.String(length=16), server_default='dialogue', nullable=False))
    op.add_column('audio_clips', sa.Column('source', sa.String(length=16), server_default='upload', nullable=False))
    op.add_column('audio_clips', sa.Column('voice', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='VoiceProfile a TTS clip was spoken with'))
    op.add_column('audio_clips', sa.Column('provider', sa.String(length=64), nullable=True, comment='the speech provider and its version, e.g. espeak-ng-1.51'))
    op.alter_column('audio_clips', 'upload_id',
               existing_type=sa.UUID(),
               nullable=True)
    # ### end Alembic commands ###
    op.drop_constraint(op.f('ck_audio_clips_ready_has_result'), 'audio_clips', type_='check')
    op.create_check_constraint(
        op.f('ck_audio_clips_ready_has_result'), 'audio_clips',
        "status <> 'ready' OR (wav_storage_key IS NOT NULL AND wav_sha256 IS NOT NULL "
        "AND duration_s IS NOT NULL AND (kind <> 'dialogue' OR cues IS NOT NULL))")
    op.create_check_constraint(op.f('ck_audio_clips_kind'), 'audio_clips', "kind IN ('dialogue', 'music', 'sfx')")
    op.create_check_constraint(op.f('ck_audio_clips_source'), 'audio_clips', "source IN ('upload', 'tts')")
    op.create_check_constraint(
        op.f('ck_audio_clips_source_input'), 'audio_clips',
        "(source = 'upload' AND upload_id IS NOT NULL) OR (source = 'tts' AND upload_id IS NULL "
        "AND voice IS NOT NULL AND dialog_text IS NOT NULL AND kind = 'dialogue')")
    op.execute(AUDIO_GUARD)
    op.execute(GUARDS)


def downgrade() -> None:
    op.execute(DROP_GUARDS)
    op.execute(AUDIO_GUARD_0010)
    op.execute("DELETE FROM audio_clips WHERE source = 'tts' OR kind <> 'dialogue'")
    for name in ('source_input', 'source', 'kind', 'ready_has_result'):
        op.drop_constraint(op.f(f'ck_audio_clips_{name}'), 'audio_clips', type_='check')
    op.create_check_constraint(
        op.f('ck_audio_clips_ready_has_result'), 'audio_clips',
        "status <> 'ready' OR (wav_storage_key IS NOT NULL AND wav_sha256 IS NOT NULL "
        "AND duration_s IS NOT NULL AND cues IS NOT NULL)")
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('audio_clips', 'upload_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.drop_column('audio_clips', 'provider')
    op.drop_column('audio_clips', 'voice')
    op.drop_column('audio_clips', 'source')
    op.drop_column('audio_clips', 'kind')
    op.drop_index(op.f('ix_render_outputs_render_job_id'), table_name='render_outputs')
    op.drop_table('render_outputs')
    op.drop_index(op.f('ix_render_job_shots_scene_version_id'), table_name='render_job_shots')
    op.drop_index(op.f('ix_render_job_shots_render_job_id'), table_name='render_job_shots')
    op.drop_index(op.f('ix_render_job_shots_job_id'), table_name='render_job_shots')
    op.drop_table('render_job_shots')
    op.drop_index(op.f('ix_render_jobs_status'), table_name='render_jobs')
    op.drop_index(op.f('ix_render_jobs_episode_version_id'), table_name='render_jobs')
    op.drop_index(op.f('ix_render_jobs_episode_id'), table_name='render_jobs')
    op.drop_table('render_jobs')
    op.drop_index(op.f('ix_episode_version_shots_scene_version_id'), table_name='episode_version_shots')
    op.drop_index(op.f('ix_episode_version_shots_episode_version_id'), table_name='episode_version_shots')
    op.drop_table('episode_version_shots')
    op.drop_index(op.f('ix_episode_audio_audio_clip_id'), table_name='episode_audio')
    op.drop_table('episode_audio')
    op.drop_index(op.f('ix_episode_assets_asset_version_id'), table_name='episode_assets')
    op.drop_table('episode_assets')
    op.drop_index(op.f('ix_shots_storyboard_id'), table_name='shots')
    op.drop_table('shots')
    op.drop_index(op.f('ix_episode_versions_episode_id'), table_name='episode_versions')
    op.drop_table('episode_versions')
    op.drop_index(op.f('ix_storyboards_script_version_id'), table_name='storyboards')
    op.drop_index(op.f('ix_storyboards_episode_id'), table_name='storyboards')
    op.drop_table('storyboards')
    op.drop_index(op.f('ix_series_members_series_id'), table_name='series_members')
    op.drop_index(op.f('ix_series_members_character_id'), table_name='series_members')
    op.drop_table('series_members')
    op.drop_index(op.f('ix_script_versions_script_id'), table_name='script_versions')
    op.drop_table('script_versions')
    op.drop_table('scripts')
    op.drop_index(op.f('ix_episode_scenes_episode_id'), table_name='episode_scenes')
    op.drop_table('episode_scenes')
    op.drop_index(op.f('ix_series_assets_series_id'), table_name='series_assets')
    op.drop_index(op.f('ix_series_assets_asset_version_id'), table_name='series_assets')
    op.drop_table('series_assets')
    op.drop_index(op.f('ix_episodes_series_id'), table_name='episodes')
    op.drop_index(op.f('ix_episodes_created_by'), table_name='episodes')
    op.drop_table('episodes')
    op.drop_index(op.f('ix_series_name'), table_name='series')
    op.drop_index(op.f('ix_series_created_by'), table_name='series')
    op.drop_table('series')
    # ### end Alembic commands ###
