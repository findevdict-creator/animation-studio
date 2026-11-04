from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.models import Base
from studio_testing.plugin import alembic_config

EXPECTED_TABLES = {
    "users", "roles", "permissions", "user_roles", "role_permissions", "asset_code_counters",
    "asset_versions_registry", "asset_files", "asset_dependencies", "jobs", "job_events", "audit_logs",
}


def test_upgrade_downgrade_upgrade(migrated):
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    engine = create_engine(migrated)
    assert not EXPECTED_TABLES & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "head")
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_models_match_migrations(migrated):
    engine = create_engine(migrated)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    engine.dispose()
    assert diff == []
