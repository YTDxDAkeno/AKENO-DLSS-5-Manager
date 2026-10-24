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


def run_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(get_config().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
