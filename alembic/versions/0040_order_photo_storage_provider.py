"""Add storage provider and key to order photos

Revision ID: 0040_order_photo_storage_provider
Revises: 0039_auth_hardening
Create Date: 2025-05-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0040_order_photo_storage_provider"
down_revision = "0039_auth_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_photos",
        sa.Column("storage_provider", sa.String(length=32), nullable=False, server_default="local"),
    )
    op.add_column(
        "order_photos",
        sa.Column("storage_key", sa.String(length=255), nullable=True),
    )

    bind = op.get_bind()
    order_photos = sa.table(
        "order_photos",
        sa.column("photo_id", sa.String(length=36)),
        sa.column("org_id", sa.String()),
        sa.column("order_id", sa.String()),
        sa.column("filename", sa.String()),
    )

    results = bind.execute(sa.select(order_photos.c.photo_id, order_photos.c.org_id, order_photos.c.order_id, order_photos.c.filename)).fetchall()
    for row in results:
        key = f"orders/{row.org_id}/{row.order_id}/{row.filename}"
        bind.execute(
            sa.update(order_photos)
            .where(order_photos.c.photo_id == row.photo_id)
            .values(storage_key=key)
        )

    with op.batch_alter_table("order_photos") as batch:
        batch.alter_column("storage_key", existing_type=sa.String(length=255), nullable=False)


def downgrade() -> None:
    op.drop_column("order_photos", "storage_key")
    op.drop_column("order_photos", "storage_provider")
