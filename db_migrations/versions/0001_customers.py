"""Initial legacy schema with full_name only.

Revision ID: 0001_customers
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_customers"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("full_name", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("customers")
