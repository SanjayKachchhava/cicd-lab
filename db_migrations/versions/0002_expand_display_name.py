"""Expand schema, keep full_name authoritative, synchronize old writers.

Revision ID: 0002_expand_display_name
Revises: 0001_customers
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_expand_display_name"
down_revision = "0001_customers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("display_name", sa.Text(), nullable=True))
    op.create_table(
        "migration_runs",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("processed_rows", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Blue (old code) writes full_name; this trigger keeps display_name synced.
    # full_name remains the authoritative column during this release.
    op.execute("""
        CREATE FUNCTION cicd_sync_display_name() RETURNS trigger AS $$
        BEGIN
            NEW.display_name := NEW.full_name;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER cicd_customers_sync_display_name
        BEFORE INSERT OR UPDATE OF full_name ON customers
        FOR EACH ROW EXECUTE FUNCTION cicd_sync_display_name();
    """)


def downgrade() -> None:
    # Downgrade intentionally destructive to the new column; never run on live DB
    # as an automatic response to failed application deployment.
    op.execute("DROP TRIGGER cicd_customers_sync_display_name ON customers")
    op.execute("DROP FUNCTION cicd_sync_display_name()")
    op.drop_table("migration_runs")
    op.drop_column("customers", "display_name")
