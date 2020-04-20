"""Add NOT NULL constraints

Revision ID: fe0646eb142a
Revises: 5b48c4a0a25d
Create Date: 2020-04-19 22:06:12.739973

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fe0646eb142a'
down_revision = '5b48c4a0a25d'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('entries', schema=None) as batch_op:
        batch_op.alter_column('sheet_id',
                              existing_type=sa.INTEGER(),
                              nullable=False)
        batch_op.alter_column('text',
                              existing_type=sa.VARCHAR(),
                              nullable=False)
        batch_op.alter_column('timestamp',
                              existing_type=sa.DATETIME(),
                              nullable=False)
        batch_op.alter_column('type',
                              existing_type=sa.VARCHAR(length=8),
                              nullable=False)
        batch_op.alter_column('user_id',
                              existing_type=sa.INTEGER(),
                              nullable=False)

    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.alter_column('chat_id',
                              existing_type=sa.BIGINT(),
                              nullable=False)
        batch_op.alter_column('is_finished',
                              existing_type=sa.BOOLEAN(),
                              nullable=False)
        batch_op.alter_column('is_showing_result_names',
                              existing_type=sa.BOOLEAN(),
                              nullable=False)
        batch_op.alter_column('is_started',
                              existing_type=sa.BOOLEAN(),
                              nullable=False)
        batch_op.alter_column('is_synchronous',
                              existing_type=sa.BOOLEAN(),
                              nullable=False)
        batch_op.alter_column('is_waiting_for_finish',
                              existing_type=sa.BOOLEAN(),
                              nullable=False)
        batch_op.alter_column('name',
                              existing_type=sa.VARCHAR(),
                              nullable=False)

    with op.batch_alter_table('sheets', schema=None) as batch_op:
        batch_op.alter_column('game_id',
                              existing_type=sa.INTEGER(),
                              nullable=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('api_id',
                              existing_type=sa.INTEGER(),
                              nullable=False)
        batch_op.alter_column('chat_id',
                              existing_type=sa.BIGINT(),
                              nullable=False)
        batch_op.alter_column('name',
                              existing_type=sa.VARCHAR(),
                              nullable=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('name',
                              existing_type=sa.VARCHAR(),
                              nullable=True)
        batch_op.alter_column('chat_id',
                              existing_type=sa.BIGINT(),
                              nullable=True)
        batch_op.alter_column('api_id',
                              existing_type=sa.INTEGER(),
                              nullable=True)

    with op.batch_alter_table('sheets', schema=None) as batch_op:
        batch_op.alter_column('game_id',
                              existing_type=sa.INTEGER(),
                              nullable=True)

    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.alter_column('name',
                              existing_type=sa.VARCHAR(),
                              nullable=True)
        batch_op.alter_column('is_waiting_for_finish',
                              existing_type=sa.BOOLEAN(),
                              nullable=True)
        batch_op.alter_column('is_synchronous',
                              existing_type=sa.BOOLEAN(),
                              nullable=True)
        batch_op.alter_column('is_started',
                              existing_type=sa.BOOLEAN(),
                              nullable=True)
        batch_op.alter_column('is_showing_result_names',
                              existing_type=sa.BOOLEAN(),
                              nullable=True)
        batch_op.alter_column('is_finished',
                              existing_type=sa.BOOLEAN(),
                              nullable=True)
        batch_op.alter_column('chat_id',
                              existing_type=sa.BIGINT(),
                              nullable=True)

    with op.batch_alter_table('entries', schema=None) as batch_op:
        batch_op.alter_column('user_id',
                              existing_type=sa.INTEGER(),
                              nullable=True)
        batch_op.alter_column('type',
                              existing_type=sa.VARCHAR(length=8),
                              nullable=True)
        batch_op.alter_column('timestamp',
                              existing_type=sa.DATETIME(),
                              nullable=True)
        batch_op.alter_column('text',
                              existing_type=sa.VARCHAR(),
                              nullable=True)
        batch_op.alter_column('sheet_id',
                              existing_type=sa.INTEGER(),
                              nullable=True)

    # ### end Alembic commands ###