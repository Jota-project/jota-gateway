"""drop system_prompt_extra column

Revision ID: 106077a95a0f
Revises: 21cb0cf4f6f9
Create Date: 2026-07-18 00:41:33.958978

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '106077a95a0f'
down_revision: Union[str, None] = '21cb0cf4f6f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("clients") as batch_op:
        batch_op.drop_column("system_prompt_extra")


def downgrade() -> None:
    with op.batch_alter_table("clients") as batch_op:
        batch_op.add_column(sa.Column("system_prompt_extra", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
