"""conversations (M19)

Revision ID: 5b1e7c2d9a40
Revises: 106077a95a0f
Create Date: 2026-11-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '5b1e7c2d9a40'
down_revision: Union[str, None] = '106077a95a0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('conversations',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('client_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('agent', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('session_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('day', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_turn_at', sa.DateTime(), nullable=True),
    sa.Column('turn_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_client_id'), 'conversations', ['client_id'], unique=False)
    op.create_index(op.f('ix_conversations_session_key'), 'conversations', ['session_key'], unique=True)
    op.create_index('uq_conversations_default_day', 'conversations', ['client_id', 'agent', 'day'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_conversations_default_day', table_name='conversations')
    op.drop_index(op.f('ix_conversations_session_key'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_client_id'), table_name='conversations')
    op.drop_table('conversations')
