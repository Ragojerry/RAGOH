from sqlalchemy import create_engine, pool

from alembic import context
from app.core.config import get_settings
from app.db.models import Base

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    # PostGIS owns these tables; never try to drop them.
    # PostGIS owns spatial_ref_sys; gps_points_* are monthly partitions managed in SQL.
    managed_elsewhere = name == "spatial_ref_sys" or (name or "").startswith("gps_points_")
    return not (type_ == "table" and managed_elsewhere)


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
