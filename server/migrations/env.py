"""Alembic environment: the URL comes from the server configuration, the tables from akeno_server.models."""

from alembic import context

from akeno_server.config import get_config
from akeno_server.db import make_engine
from akeno_server.models import Base

target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(url=get_config().database_url, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def _sqlite_batch_mode(connection) -> None:
    """SQLite changes a column by copying its table (batch mode). With foreign keys enforced, dropping the old copy
    of a table that other rows point to fails, so they are off while migrating (set before any transaction;
    the copies keep every row and key). PostgreSQL alters tables in place and is not affected."""
    if connection.dialect.name == "sqlite":
        connection.connection.driver_connection.execute("PRAGMA foreign_keys=OFF")


def run_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(get_config().database_url)
    with engine.connect() as connection:
        _sqlite_batch_mode(connection)
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
