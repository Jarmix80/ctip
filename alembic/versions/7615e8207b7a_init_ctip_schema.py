"""init ctip schema

Revision ID: 7615e8207b7a
Revises:
Create Date: 2025-10-11 01:09:08.359881

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models import Base

# revision identifiers, used by Alembic.
revision: str = "7615e8207b7a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ctip"))
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
    bind.execute(sa.text("DROP SCHEMA IF EXISTS ctip CASCADE"))
