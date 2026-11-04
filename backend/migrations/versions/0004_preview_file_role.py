"""asset file role "preview" (expression sheets, contact sheets)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OLD = ("blend", "spec", "thumbnail", "turntable", "glb", "texture", "manifest", "log", "report")
NEW = (*OLD, "preview")


def upgrade() -> None:
    op.drop_constraint(op.f("ck_asset_files_role"), "asset_files", type_="check")
    op.create_check_constraint(op.f("ck_asset_files_role"), "asset_files", f"role IN {NEW}")


def downgrade() -> None:
    op.execute("DELETE FROM asset_files WHERE role = 'preview'")
    op.drop_constraint(op.f("ck_asset_files_role"), "asset_files", type_="check")
    op.create_check_constraint(op.f("ck_asset_files_role"), "asset_files", f"role IN {OLD}")
