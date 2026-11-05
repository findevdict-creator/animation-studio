"""Permission names and the default role grants (SRS 3).

The baseline migration seeds the database from the same table; ``tests/test_permissions.py``
fails if the two drift apart. Authorisation reads the database, so an admin can change
grants without a deploy.
"""

from __future__ import annotations

PERMISSIONS: tuple[str, ...] = (
    "asset.read", "asset.create", "asset.edit_identity", "asset.edit_draft", "asset.submit",
    "asset.review", "asset.publish", "ai.use", "series.manage", "episode.manage", "render.submit",
    "job.read", "job.submit", "job.cancel", "comment.write", "scene.edit", "admin.users", "admin.ai",
    "admin.workers", "audit.read",
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(PERMISSIONS),
    "creator": frozenset({
        "asset.read", "asset.create", "asset.edit_identity", "asset.edit_draft", "asset.submit",
        "ai.use", "job.read", "job.submit", "job.cancel", "comment.write", "scene.edit",
    }),
    "director": frozenset({
        "asset.read", "asset.review", "asset.publish", "asset.edit_identity", "ai.use",
        "series.manage", "episode.manage", "render.submit", "job.read", "job.submit", "job.cancel",
        "comment.write", "scene.edit",
    }),
    "reviewer": frozenset({"asset.read", "asset.review", "job.read", "comment.write"}),
}
