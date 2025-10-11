"""Deklaracja bazowa SQLAlchemy dla schematu `ctip`."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Bazowa klasa modeli ORM."""

    metadata = MetaData(schema="ctip")
