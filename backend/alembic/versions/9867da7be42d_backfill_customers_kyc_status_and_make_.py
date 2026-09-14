"""backfill customers.kyc_status and make it required

Revision ID: 9867da7be42d
Revises: 20b73cd35c99
Create Date: 2026-09-14 14:53:06.336939

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision = '9867da7be42d'
down_revision = '20b73cd35c99'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE customers SET kyc_status = 'pending' WHERE kyc_status IS NULL")
    op.alter_column('customers', 'kyc_status', nullable=False)


def downgrade() -> None:
    op.alter_column('customers', 'kyc_status', nullable=True)
