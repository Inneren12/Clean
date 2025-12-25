from alembic import op
import sqlalchemy as sa
import uuid


# revision identifiers, used by Alembic.
revision = "0006_referrals"
down_revision = "0005_deposits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name if bind else ""

    op.add_column("leads", sa.Column("referral_code", sa.String(length=16), nullable=True))
    op.add_column("bookings", sa.Column("referral_code_applied", sa.String(length=16), nullable=True))
    op.add_column(
        "bookings",
        sa.Column(
            "referral_credit_cents",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.create_table(
        "referral_redemptions",
        sa.Column(
            "redemption_id",
            sa.String(length=36),
            primary_key=True,
            default=lambda: str(uuid.uuid4()),
        ),
        sa.Column("referrer_lead_id", sa.String(length=36), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("referred_lead_id", sa.String(length=36), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("booking_id", sa.String(length=36), nullable=True),
        sa.Column("referral_code", sa.String(length=16), nullable=False),
        sa.Column(
            "credit_cents",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("referred_lead_id"),
    )
    op.create_index(op.f("ix_referrals_referrer"), "referral_redemptions", ["referrer_lead_id"], unique=False)

    connection = op.get_bind()
    leads = connection.execute(sa.text("SELECT lead_id FROM leads")).fetchall()
    for (lead_id,) in leads:
        generated = (lead_id or str(uuid.uuid4())).replace("-", "")[:12]
        connection.execute(
            sa.text("UPDATE leads SET referral_code = :code WHERE lead_id = :lead_id"),
            {"code": generated, "lead_id": lead_id},
        )

    with op.batch_alter_table("leads") as batch:
        batch.alter_column("referral_code", nullable=False)
        batch.create_unique_constraint("uq_leads_referral_code", ["referral_code"])

    if dialect_name != "sqlite":
        op.alter_column("bookings", "referral_credit_cents", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_leads_referral_code", "leads", type_="unique")
    op.drop_index(op.f("ix_referrals_referrer"), table_name="referral_redemptions")
    op.drop_table("referral_redemptions")
    op.drop_column("bookings", "referral_credit_cents")
    op.drop_column("bookings", "referral_code_applied")
    op.drop_column("leads", "referral_code")
