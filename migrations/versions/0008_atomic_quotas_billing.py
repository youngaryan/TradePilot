from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_atomic_quotas_billing"
down_revision = "0007_auth_mfa_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("stripe_event_created_at", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("subscriptions", sa.Column("stripe_event_id", sa.Text(), nullable=True))
    op.add_column("stripe_events", sa.Column("payload_hash", sa.Text(), nullable=False, server_default=""))
    op.add_column("stripe_events", sa.Column("event_created_at", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("stripe_events", sa.Column("status", sa.Text(), nullable=False, server_default="processed"))
    op.add_column("stripe_events", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("stripe_events", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("stripe_events", sa.Column("claim_token", sa.Text(), nullable=True))
    op.add_column("stripe_events", sa.Column("claimed_at_utc", sa.Text(), nullable=True))
    op.add_column("stripe_events", sa.Column("last_error_code", sa.Text(), nullable=True))
    op.add_column("stripe_events", sa.Column("created_at_utc", sa.Text(), nullable=False, server_default=""))
    op.add_column("stripe_events", sa.Column("updated_at_utc", sa.Text(), nullable=False, server_default=""))
    op.create_index("idx_stripe_events_status_updated", "stripe_events", ["status", "updated_at_utc"])
    op.create_table(
        "quota_usage_counters",
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("window_start_utc", sa.Text(), nullable=False),
        sa.Column("window_end_utc", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "feature", "window_start_utc", "window_end_utc"),
    )
    op.create_index(
        "idx_quota_counters_window",
        "quota_usage_counters",
        ["window_end_utc", "organization_id", "feature"],
    )


def downgrade() -> None:
    op.drop_index("idx_quota_counters_window", table_name="quota_usage_counters")
    op.drop_table("quota_usage_counters")
    op.drop_index("idx_stripe_events_status_updated", table_name="stripe_events")
    for column in (
        "updated_at_utc",
        "created_at_utc",
        "last_error_code",
        "claimed_at_utc",
        "claim_token",
        "max_attempts",
        "attempt_count",
        "status",
        "event_created_at",
        "payload_hash",
    ):
        op.drop_column("stripe_events", column)
    op.drop_column("subscriptions", "stripe_event_id")
    op.drop_column("subscriptions", "stripe_event_created_at")
