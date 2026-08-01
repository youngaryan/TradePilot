from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_strategy_marketplace"
down_revision = "0008_atomic_quotas_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_listings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("publisher_organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("publisher_user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_user_strategy_id", sa.Text(), sa.ForeignKey("user_strategies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.Column("published_at_utc", sa.Text(), nullable=True),
        sa.Column("archived_at_utc", sa.Text(), nullable=True),
    )
    op.create_index("idx_strategy_listings_public", "strategy_listings", ["status", "visibility", "published_at_utc"])
    op.create_index("idx_strategy_listings_publisher", "strategy_listings", ["publisher_organization_id", "updated_at_utc"])
    op.create_table(
        "strategy_listing_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("listing_id", sa.Text(), sa.ForeignKey("strategy_listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("strategy_spec_json", sa.Text(), nullable=False),
        sa.Column("catalog_snapshot_json", sa.Text(), nullable=False),
        sa.Column("validation_snapshot_json", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("source_strategy_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.UniqueConstraint("listing_id", "version", name="uq_strategy_listing_version"),
        sa.UniqueConstraint("listing_id", "content_hash", name="uq_strategy_listing_content_hash"),
    )
    op.create_foreign_key(
        "fk_strategy_listings_current_version",
        "strategy_listings",
        "strategy_listing_versions",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "strategy_marketplace_subscriptions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("subscriber_organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscriber_user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("listing_id", sa.Text(), sa.ForeignKey("strategy_listings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pinned_listing_version_id", sa.Text(), sa.ForeignKey("strategy_listing_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.Column("cancelled_at_utc", sa.Text(), nullable=True),
        sa.UniqueConstraint("subscriber_organization_id", "listing_id", name="uq_strategy_marketplace_subscription"),
    )
    op.create_index("idx_marketplace_subscriptions_owner", "strategy_marketplace_subscriptions", ["subscriber_organization_id", "status", "updated_at_utc"])


def downgrade() -> None:
    op.drop_index("idx_marketplace_subscriptions_owner", table_name="strategy_marketplace_subscriptions")
    op.drop_table("strategy_marketplace_subscriptions")
    op.drop_constraint("fk_strategy_listings_current_version", "strategy_listings", type_="foreignkey")
    op.drop_table("strategy_listing_versions")
    op.drop_index("idx_strategy_listings_publisher", table_name="strategy_listings")
    op.drop_index("idx_strategy_listings_public", table_name="strategy_listings")
    op.drop_table("strategy_listings")
