from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_auth_mfa_hardening"
down_revision = "0006_paper_deployments_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_pending_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("mfa_last_counter", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mfa_last_counter")
    op.drop_column("users", "mfa_pending_secret")

