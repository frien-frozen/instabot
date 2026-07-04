"""Idempotent Alembic helpers for partially migrated production databases."""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


def _inspector():
    return inspect(op.get_bind())


def table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def column_names(table_name: str) -> set[str]:
    if not table_exists(table_name):
        return set()
    return {column["name"] for column in _inspector().get_columns(table_name)}


def index_names(table_name: str) -> set[str]:
    if not table_exists(table_name):
        return set()
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def add_column_if_missing(table_name: str, column) -> None:
    if column.name not in column_names(table_name):
        op.add_column(table_name, column)


def create_index_if_missing(index_name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if index_name not in index_names(table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def drop_index_if_exists(index_name: str, table_name: str) -> None:
    if index_name in index_names(table_name):
        op.drop_index(index_name, table_name=table_name)


def drop_column_if_exists(table_name: str, column_name: str) -> None:
    if column_name in column_names(table_name):
        op.drop_column(table_name, column_name)


def drop_table_if_exists(table_name: str) -> None:
    if table_exists(table_name):
        op.drop_table(table_name)
