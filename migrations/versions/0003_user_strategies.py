from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_user_strategies"
down_revision = "0002_tenant_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_strategies",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("root_strategy_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("validation_json", sa.Text(), nullable=False),
        sa.Column("approval_json", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.Column("approved_at_utc", sa.Text()),
        sa.Column("disabled_at_utc", sa.Text()),
        sa.Column("deleted_at_utc", sa.Text()),
        sa.Column("backtest_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("idx_user_strategies_owner_status", "user_strategies", ["organization_id", "owner_user_id", "status", "updated_at_utc"])
    op.create_index("idx_user_strategies_admin", "user_strategies", ["status", "risk_level", "created_at_utc"])


def downgrade() -> None:
    op.drop_index("idx_user_strategies_admin", table_name="user_strategies")
    op.drop_index("idx_user_strategies_owner_status", table_name="user_strategies")
    op.drop_table("user_strategies")
