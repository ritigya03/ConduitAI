"""add server default for customers.kyc_status

Revision ID: 0f019c3956ec
Revises: 9867da7be42d
Create Date: 2026-09-14 14:56:47.075130

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision = '0f019c3956ec'
down_revision = '9867da7be42d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Lets an INSERT built from an older confirmed mapping spec (one that
    # never mapped kyc_status, because it was created before this field
    # existed) succeed without explicitly setting the column -- app.loader
    # only ever inserts the columns present in a built record's `values`,
    # so a column with no server-side default would 23502 (not-null
    # violation) on every old-spec load the moment this field became
    # required. Found by running the full suite for real after adding the
    # column (Day 6).
    op.alter_column('customers', 'kyc_status', server_default='pending')


def downgrade() -> None:
    op.alter_column('customers', 'kyc_status', server_default=None)
