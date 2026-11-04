from sqlalchemy import text

from app.core.permissions import PERMISSIONS, ROLE_PERMISSIONS


def test_seeded_grants_match_code(connection):
    rows = connection.execute(
        text(
            "SELECT r.name, p.name FROM roles r JOIN role_permissions rp ON rp.role_id = r.id "
            "JOIN permissions p ON p.id = rp.permission_id"
        )
    ).all()
    seeded: dict[str, set[str]] = {}
    for role, perm in rows:
        seeded.setdefault(role, set()).add(perm)
    assert seeded == {k: set(v) for k, v in ROLE_PERMISSIONS.items()}
    names = set(connection.execute(text("SELECT name FROM permissions")).scalars())
    assert names == set(PERMISSIONS)


def test_reviewers_cannot_publish_and_creators_cannot_review():
    assert "asset.publish" not in ROLE_PERMISSIONS["reviewer"]
    assert "asset.publish" not in ROLE_PERMISSIONS["creator"]
    assert "asset.review" not in ROLE_PERMISSIONS["creator"]
