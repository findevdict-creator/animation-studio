"""immutability guards, append-only audit log, seed roles and permissions

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04

Architecture section 6.2: the database refuses to change a published or deprecated
version, its files, or any audit log row, whatever the application code does.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

GUARDS = r"""
CREATE FUNCTION studio_guard_asset_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    allowed boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('published', 'deprecated') THEN
            RAISE EXCEPTION 'asset version % is %, it cannot be deleted', OLD.id, OLD.status
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF NEW.id <> OLD.id OR NEW.kind <> OLD.kind OR NEW.asset_id <> OLD.asset_id
       OR NEW.display_code <> OLD.display_code THEN
        RAISE EXCEPTION 'asset version identity columns are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status <> OLD.status THEN
        allowed := (OLD.status, NEW.status) IN (
            ('draft', 'review'), ('review', 'draft'), ('review', 'published'),
            ('published', 'deprecated'), ('deprecated', 'published'));
        IF NOT allowed THEN
            RAISE EXCEPTION 'asset version status % -> % is not allowed', OLD.status, NEW.status
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;

    IF OLD.status IN ('published', 'deprecated') THEN
        IF NEW.major IS DISTINCT FROM OLD.major OR NEW.minor IS DISTINCT FROM OLD.minor
           OR NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'asset version % is %, only its status can change', OLD.id, OLD.status
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER asset_versions_registry_guard
    BEFORE UPDATE OR DELETE ON asset_versions_registry
    FOR EACH ROW EXECUTE FUNCTION studio_guard_asset_version();

CREATE FUNCTION studio_guard_asset_file() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version_status text;
    target uuid;
BEGIN
    target := CASE WHEN TG_OP = 'INSERT' THEN NEW.version_id ELSE OLD.version_id END;
    SELECT status INTO version_status FROM asset_versions_registry WHERE id = target;
    IF version_status IN ('published', 'deprecated') THEN
        RAISE EXCEPTION 'files of asset version % (%) cannot be %', target, version_status, lower(TG_OP)
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.version_id <> OLD.version_id THEN
        SELECT status INTO version_status FROM asset_versions_registry WHERE id = NEW.version_id;
        IF version_status IN ('published', 'deprecated') THEN
            RAISE EXCEPTION 'files cannot be moved into published version %', NEW.version_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER asset_files_guard
    BEFORE INSERT OR UPDATE OR DELETE ON asset_files
    FOR EACH ROW EXECUTE FUNCTION studio_guard_asset_file();

CREATE FUNCTION studio_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END $$;

CREATE TRIGGER audit_logs_append_only
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION studio_append_only();
"""

DROP_GUARDS = """
DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs;
DROP TRIGGER IF EXISTS asset_files_guard ON asset_files;
DROP TRIGGER IF EXISTS asset_versions_registry_guard ON asset_versions_registry;
DROP FUNCTION IF EXISTS studio_append_only();
DROP FUNCTION IF EXISTS studio_guard_asset_file();
DROP FUNCTION IF EXISTS studio_guard_asset_version();
"""

# SRS section 3 roles. Kept in sync with app.core.permissions.ROLE_PERMISSIONS by a test.
PERMISSIONS = {
    "asset.read": "View library assets and their versions",
    "asset.create": "Create assets and drafts, run generation jobs",
    "asset.edit_identity": "Rename, tag and set the recommended version",
    "asset.edit_draft": "Edit draft specifications",
    "asset.submit": "Submit a draft for review",
    "asset.review": "Approve or reject a version in review",
    "asset.publish": "Publish and deprecate versions",
    "ai.use": "Run AI interpretation and generation tasks",
    "series.manage": "Create and edit series",
    "episode.manage": "Create episodes, approve scripts and storyboards",
    "render.submit": "Submit and cancel render jobs",
    "job.read": "View jobs and their logs",
    "job.submit": "Submit background jobs",
    "job.cancel": "Cancel or retry jobs",
    "comment.write": "Comment on scenes and deliverables",
    "admin.users": "Manage users and roles",
    "admin.ai": "Configure AI providers and models",
    "admin.workers": "View and configure workers and storage",
    "audit.read": "Read the audit log",
}

ROLES = {
    "admin": ("Administrator", sorted(PERMISSIONS)),
    "creator": ("Creator / Artist", [
        "asset.read", "asset.create", "asset.edit_identity", "asset.edit_draft", "asset.submit",
        "ai.use", "job.read", "job.submit", "job.cancel", "comment.write",
    ]),
    "director": ("Director / Producer", [
        "asset.read", "asset.review", "asset.publish", "asset.edit_identity", "ai.use",
        "series.manage", "episode.manage", "render.submit", "job.read", "job.submit", "job.cancel",
        "comment.write",
    ]),
    "reviewer": ("Viewer / Reviewer", ["asset.read", "asset.review", "job.read", "comment.write"]),
}


def upgrade() -> None:
    op.execute(GUARDS)
    perms = sa.table("permissions", sa.column("id", sa.UUID), sa.column("name", sa.String), sa.column("description", sa.Text))
    roles = sa.table("roles", sa.column("id", sa.UUID), sa.column("name", sa.String), sa.column("description", sa.Text))
    links = sa.table("role_permissions", sa.column("role_id", sa.UUID), sa.column("permission_id", sa.UUID))
    perm_ids = {name: uuid.uuid5(uuid.NAMESPACE_URL, f"studio:permission:{name}") for name in PERMISSIONS}
    op.bulk_insert(perms, [{"id": perm_ids[n], "name": n, "description": d} for n, d in PERMISSIONS.items()])
    role_rows, link_rows = [], []
    for name, (description, granted) in ROLES.items():
        rid = uuid.uuid5(uuid.NAMESPACE_URL, f"studio:role:{name}")
        role_rows.append({"id": rid, "name": name, "description": description})
        link_rows += [{"role_id": rid, "permission_id": perm_ids[p]} for p in granted]
    op.bulk_insert(roles, role_rows)
    op.bulk_insert(links, link_rows)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions; DELETE FROM user_roles; DELETE FROM roles; DELETE FROM permissions;")
    op.execute(DROP_GUARDS)
