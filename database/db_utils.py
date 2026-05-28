"""Database utilities for the FRTB Risk Monitor.

All database access in this project goes through SQLAlchemy (never raw psycopg2
cursors). This module centralises:

  * loading connection credentials from the project ``.env`` file,
  * building SQLAlchemy engines,
  * bootstrapping the ``frtb_monitor`` database and its schema,
  * a generic idempotent "upsert" helper used by every pipeline module,
  * a thin query helper that returns pandas DataFrames.

Credentials are read from environment variables (see ``.env``):
``DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional, Sequence
from urllib.parse import quote_plus

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Project root is the folder that contains the .env file (one level above this
# database/ package).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Load environment variables from the project .env exactly once on import.
load_dotenv(ENV_PATH)


def _require_env(name: str) -> str:
    """Return the value of an environment variable or raise a clear error.

    Inputs:
        name: the environment variable name to read.
    Output:
        The variable's value as a string.
    Raises:
        RuntimeError if the variable is missing or still set to a placeholder.
    """
    value = os.getenv(name)
    if not value or value.startswith("replace_with_"):
        raise RuntimeError(
            f"Environment variable '{name}' is not set. "
            f"Edit {ENV_PATH} and provide a real value."
        )
    return value


def _connection_url(dbname: Optional[str] = None) -> str:
    """Build a SQLAlchemy PostgreSQL connection URL from environment variables.

    Inputs:
        dbname: database name to connect to. Defaults to DB_NAME from .env.
                Pass 'postgres' to connect to the maintenance database (used
                when creating the project database for the first time).
    Output:
        A psycopg2 SQLAlchemy URL string with the password URL-encoded.
    """
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = _require_env("DB_USER")
    password = _require_env("DB_PASSWORD")
    database = dbname or os.getenv("DB_NAME", "frtb_monitor")
    # quote_plus keeps passwords with special characters (@, :, /) URL-safe.
    return (
        f"postgresql+psycopg2://{user}:{quote_plus(password)}"
        f"@{host}:{port}/{database}"
    )


def get_engine(dbname: Optional[str] = None) -> Engine:
    """Create a SQLAlchemy engine for the requested database.

    Inputs:
        dbname: optional database name override. Defaults to DB_NAME from .env.
    Output:
        A configured SQLAlchemy Engine. ``pool_pre_ping`` recycles dead
        connections so scheduled daily runs survive idle disconnects.
    """
    return create_engine(_connection_url(dbname), pool_pre_ping=True, future=True)


def create_database() -> None:
    """Ensure the ``frtb_monitor`` database exists.

    Connects to the default ``postgres`` maintenance database using an
    AUTOCOMMIT connection (CREATE DATABASE cannot run inside a transaction)
    and creates the target database only if it does not already exist.

    Inputs:  none (reads target name from DB_NAME).
    Output:  None. Side effect: the database exists after this returns.
    """
    target = os.getenv("DB_NAME", "frtb_monitor")
    admin_engine = create_engine(
        _connection_url("postgres"), isolation_level="AUTOCOMMIT", future=True
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target},
            ).scalar()
            if exists:
                print(f"[db] Database '{target}' already exists.")
            else:
                # Identifier cannot be bound as a parameter; target comes from
                # our own config (.env), not user input, so this is safe.
                conn.execute(text(f'CREATE DATABASE "{target}"'))
                print(f"[db] Created database '{target}'.")
    finally:
        admin_engine.dispose()


def init_schema() -> None:
    """Run schema.sql against the project database to create all tables.

    The schema is idempotent (CREATE TABLE IF NOT EXISTS), so this is safe to
    call on every setup. Statements are split on ';' and executed in order.

    Inputs:  none.
    Output:  None. Side effect: all tables and indexes exist after this returns.
    """
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    engine = get_engine()
    try:
        with engine.begin() as conn:
            for statement in _split_sql(sql):
                conn.execute(text(statement))
        print(f"[db] Schema applied from {SCHEMA_PATH.name}.")
    finally:
        engine.dispose()


def _split_sql(sql: str) -> Iterable[str]:
    """Split a SQL script into individual executable statements.

    Strips out full-line ``--`` comments and blank lines, then splits on the
    semicolon terminator. Adequate for our schema, which contains no semicolons
    inside string literals or function bodies.

    Inputs:
        sql: the full text of a .sql file.
    Output:
        An iterator of non-empty SQL statements (without trailing semicolons).
    """
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    for statement in cleaned.split(";"):
        if statement.strip():
            yield statement.strip()


def upsert_dataframe(
    df: pd.DataFrame,
    table: str,
    conflict_cols: Sequence[str],
    update_cols: Optional[Sequence[str]] = None,
    engine: Optional[Engine] = None,
) -> int:
    """Insert a DataFrame into a table, updating rows that already exist.

    Uses PostgreSQL ``INSERT ... ON CONFLICT (...) DO UPDATE`` so that re-running
    the pipeline for a date overwrites that date's rows instead of failing on
    the UNIQUE constraint. This is what makes the whole pipeline idempotent.

    Inputs:
        df:            DataFrame whose columns map to table columns.
        table:         target table name.
        conflict_cols: columns forming the UNIQUE / PK constraint to match on.
        update_cols:   columns to overwrite on conflict. Defaults to every
                       column not in conflict_cols.
        engine:        optional existing engine; one is created/closed if None.
    Output:
        The number of rows submitted (len(df)).
    """
    if df.empty:
        return 0

    own_engine = engine is None
    engine = engine or get_engine()

    columns = list(df.columns)
    if update_cols is None:
        update_cols = [c for c in columns if c not in conflict_cols]

    col_list = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    conflict_list = ", ".join(conflict_cols)
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    statement = text(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_list}) DO UPDATE SET {set_clause}"
    )

    # Convert to a list of plain dicts; replace pandas/numpy NaN with None so
    # they become SQL NULLs rather than the float 'nan'.
    records = df.astype(object).where(pd.notnull(df), None).to_dict("records")

    try:
        with engine.begin() as conn:
            conn.execute(statement, records)
    finally:
        if own_engine:
            engine.dispose()

    return len(records)


def run_query(
    sql: str,
    params: Optional[dict] = None,
    engine: Optional[Engine] = None,
) -> pd.DataFrame:
    """Run a read-only SQL query and return the result as a DataFrame.

    Inputs:
        sql:    a SQL SELECT statement, optionally with :named bind parameters.
        params: dict of bind parameter values.
        engine: optional existing engine; one is created/closed if None.
    Output:
        A pandas DataFrame of the query result (empty if no rows).
    """
    own_engine = engine is None
    engine = engine or get_engine()
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params or {})
    finally:
        if own_engine:
            engine.dispose()


if __name__ == "__main__":
    # Running this module directly bootstraps the database and schema, then
    # prints a connectivity check. Safe to run repeatedly.
    create_database()
    init_schema()
    tables = run_query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    )
    print("[db] Tables present:")
    print(tables.to_string(index=False))
