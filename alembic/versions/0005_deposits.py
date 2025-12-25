"""deposit policy and stripe fields"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005_deposits"
down_revision = "0004_lead_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name if bind else ""
    op.add_column("bookings", sa.Column("deposit_required", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("bookings", sa.Column("deposit_cents", sa.Integer(), nullable=True))
    op.add_column("bookings", sa.Column("deposit_policy", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("bookings", sa.Column("deposit_status", sa.String(length=32), nullable=True))
    op.add_column("bookings", sa.Column("stripe_checkout_session_id", sa.String(length=255), nullable=True))
    op.add_column("bookings", sa.Column("stripe_payment_intent_id", sa.String(length=255), nullable=True))
    op.create_index(
        op.f("ix_bookings_checkout_session"), "bookings", ["stripe_checkout_session_id"], unique=False
    )
    if dialect_name != "sqlite":
        op.alter_column("bookings", "deposit_required", server_default=None)
        op.alter_column("bookings", "deposit_policy", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_bookings_checkout_session"), table_name="bookings")
    op.drop_column("bookings", "stripe_payment_intent_id")
    op.drop_column("bookings", "stripe_checkout_session_id")
    op.drop_column("bookings", "deposit_status")
    op.drop_column("bookings", "deposit_policy")
    op.drop_column("bookings", "deposit_cents")
    op.drop_column("bookings", "deposit_required")
