"""add lead status column"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_lead_status"
down_revision = "0003_email_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="NEW"),
    )
    op.execute("UPDATE leads SET status = 'NEW' WHERE status IS NULL")


def downgrade() -> None:
    op.drop_column("leads", "status")
