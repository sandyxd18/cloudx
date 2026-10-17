"""image size and virtual_size to BIGINT (images over 2 GiB overflowed int32)

Revision ID: image_0003
Revises: image_0002
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "image_0003"
down_revision = "image_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("image_images") as batch:
        batch.alter_column("size", type_=sa.BigInteger(), existing_nullable=False)
        batch.alter_column("virtual_size", type_=sa.BigInteger(), existing_nullable=True)

def downgrade() -> None:
    pass