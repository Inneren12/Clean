"""add referral codes and credits

Revision ID: 0007_referrals
Revises: 0006_event_logs
Create Date: 2025-02-27 00:00:00
"""

from alembic import op
import sqlalchemy as sa
import secrets
import string


revision = "0007_referrals"
down_revision = "0006_event_logs"
branch_labels = None
depends_on = None


CHARSET = string.ascii_uppercase + string.digits
CODE_LENGTH = 8


def _generate_code(existing: set[str]) -> str:
    while True:
        candidate = "".join(secrets.choice(CHARSET) for _ in range(CODE_LENGTH))
        if candidate not in existing:
            return candidate


def upgrade() -> None:
    op.add_column("leads", sa.Column("referral_code", sa.String(length=16), nullable=True))
    op.add_column("leads", sa.Column("referred_by_code", sa.String(length=16), nullable=True))

    op.create_table(
        "referral_credits",
        sa.Column("credit_id", sa.String(length=36), primary_key=True),
        sa.Column("referrer_lead_id", sa.String(length=36), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("referred_lead_id", sa.String(length=36), sa.ForeignKey("leads.lead_id"), nullable=False),
        sa.Column("applied_code", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("referred_lead_id", name="uq_referral_credits_referred_lead"),
    )

    bind = op.get_bind()
    results = bind.execute(sa.text("SELECT lead_id FROM leads")).fetchall()
    existing_codes: set[str] = set()
    for row in results:
        code = _generate_code(existing_codes)
        existing_codes.add(code)
        bind.execute(
            sa.text("UPDATE leads SET referral_code = :code WHERE lead_id = :lead_id"),
            {"code": code, "lead_id": row.lead_id},
        )

    with op.batch_alter_table("leads") as batch:
        batch.alter_column("referral_code", nullable=False, existing_type=sa.String(length=16))
        batch.create_unique_constraint("uq_leads_referral_code", ["referral_code"])


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.drop_constraint("uq_leads_referral_code", type_="unique")
        batch.drop_column("referred_by_code")
        batch.drop_column("referral_code")
    op.drop_table("referral_credits")
