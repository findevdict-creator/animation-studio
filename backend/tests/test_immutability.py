"""Database-level guards (architecture 6.2). These go straight to SQL on purpose: the point
is that application bugs cannot get past them."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from studio_contracts.ids import uuid7

SHA = "a" * 64


def make_version(conn, status="draft", major=None, minor=None):
    vid = uuid7()
    conn.execute(
        text(
            "INSERT INTO asset_versions_registry (id, kind, asset_id, display_code, status, major, minor) "
            "VALUES (:id, 'test_asset', :aid, 'TEST-001', 'draft', NULL, NULL)"
        ),
        {"id": vid, "aid": uuid7()},
    )
    if status != "draft":
        conn.execute(text("UPDATE asset_versions_registry SET status='review' WHERE id=:id"), {"id": vid})
    if status in ("published", "deprecated"):
        conn.execute(
            text("UPDATE asset_versions_registry SET status='published', major=:ma, minor=:mi WHERE id=:id"),
            {"id": vid, "ma": major or 1, "mi": minor or 0},
        )
    if status == "deprecated":
        conn.execute(text("UPDATE asset_versions_registry SET status='deprecated' WHERE id=:id"), {"id": vid})
    return vid


def add_file(conn, vid, key="f.blend"):
    conn.execute(
        text(
            "INSERT INTO asset_files (id, version_id, role, storage_key, sha256, size_bytes, mime_type) "
            "VALUES (:id, :vid, 'blend', :key, :sha, 10, 'application/x-blender')"
        ),
        {"id": uuid7(), "vid": vid, "key": key, "sha": SHA},
    )


def refused(conn, sql, **params):
    sp = conn.begin_nested()
    with pytest.raises(IntegrityError):
        conn.execute(text(sql), params)
    sp.rollback()


def test_draft_can_change_freely(connection):
    vid = make_version(connection)
    add_file(connection, vid)
    connection.execute(text("UPDATE asset_files SET size_bytes=20 WHERE version_id=:v"), {"v": vid})
    connection.execute(text("DELETE FROM asset_files WHERE version_id=:v"), {"v": vid})
    connection.execute(text("DELETE FROM asset_versions_registry WHERE id=:v"), {"v": vid})


@pytest.mark.parametrize("status", ["published", "deprecated"])
def test_published_version_is_frozen(connection, status):
    vid = make_version(connection, "review")
    add_file(connection, vid)
    connection.execute(
        text("UPDATE asset_versions_registry SET status='published', major=1, minor=0 WHERE id=:v"), {"v": vid}
    )
    if status == "deprecated":
        connection.execute(text("UPDATE asset_versions_registry SET status='deprecated' WHERE id=:v"), {"v": vid})
    refused(connection, "UPDATE asset_versions_registry SET minor=1 WHERE id=:v", v=vid)
    refused(connection, "UPDATE asset_versions_registry SET display_code='TEST-999' WHERE id=:v", v=vid)
    refused(connection, "UPDATE asset_versions_registry SET status='draft' WHERE id=:v", v=vid)
    refused(connection, "DELETE FROM asset_versions_registry WHERE id=:v", v=vid)
    refused(connection, "UPDATE asset_files SET sha256=:s WHERE version_id=:v", v=vid, s="b" * 64)
    refused(connection, "DELETE FROM asset_files WHERE version_id=:v", v=vid)
    refused(
        connection,
        "INSERT INTO asset_files (id, version_id, role, storage_key, sha256, size_bytes, mime_type) "
        "VALUES (:id, :v, 'blend', 'late.blend', :s, 1, 'x')",
        id=uuid7(), v=vid, s=SHA,
    )


def test_deprecate_and_undeprecate_are_allowed(connection):
    vid = make_version(connection, "published")
    connection.execute(text("UPDATE asset_versions_registry SET status='deprecated' WHERE id=:v"), {"v": vid})
    connection.execute(text("UPDATE asset_versions_registry SET status='published' WHERE id=:v"), {"v": vid})


def test_illegal_transitions_refused(connection):
    vid = make_version(connection)
    refused(connection, "UPDATE asset_versions_registry SET status='published', major=1, minor=0 WHERE id=:v", v=vid)
    refused(connection, "UPDATE asset_versions_registry SET status='deprecated' WHERE id=:v", v=vid)


def test_published_needs_a_number_and_numbers_are_unique(connection):
    vid = make_version(connection, "review")
    refused(connection, "UPDATE asset_versions_registry SET status='published' WHERE id=:v", v=vid)
    asset = connection.execute(text("SELECT asset_id FROM asset_versions_registry WHERE id=:v"), {"v": vid}).scalar()
    connection.execute(
        text("UPDATE asset_versions_registry SET status='published', major=1, minor=0 WHERE id=:v"), {"v": vid}
    )
    other = uuid7()
    connection.execute(
        text(
            "INSERT INTO asset_versions_registry (id, kind, asset_id, display_code, status) "
            "VALUES (:id, 'test_asset', :aid, 'TEST-001', 'review')"
        ),
        {"id": other, "aid": asset},
    )
    refused(connection, "UPDATE asset_versions_registry SET status='published', major=1, minor=0 WHERE id=:v", v=other)


def test_files_cannot_move_into_a_published_version(connection):
    draft = make_version(connection)
    published = make_version(connection, "published")
    add_file(connection, draft)
    refused(connection, "UPDATE asset_files SET version_id=:p WHERE version_id=:d", p=published, d=draft)


def test_audit_log_is_append_only(connection):
    connection.execute(text("INSERT INTO audit_logs (action, target_kind) VALUES ('test', 'x')"))
    refused(connection, "UPDATE audit_logs SET action='changed'")
    refused(connection, "DELETE FROM audit_logs")
