from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_secure_v1"
down_revision = None
branch_labels = None
depends_on = None


JSON_TEXT = sa.Text()


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="user"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.Column("last_login_at_utc", sa.Text()),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("expires_at_utc", sa.Text()),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("owner_user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("billing_email", sa.Text()),
        sa.Column("stripe_customer_id", sa.Text()),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_table(
        "organization_members",
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text()),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("request_json", JSON_TEXT, nullable=False),
        sa.Column("payload_json", JSON_TEXT, nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.Column("started_at_utc", sa.Text()),
        sa.Column("finished_at_utc", sa.Text()),
    )
    op.create_index("idx_jobs_kind_created", "jobs", ["kind", "created_at_utc"])
    op.create_table(
        "datasets",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("provider_json", JSON_TEXT, nullable=False),
        sa.Column("schema_json", JSON_TEXT, nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_datasets_org_updated", "datasets", ["organization_id", "updated_at_utc"])
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("stripe_customer_id", sa.Text()),
        sa.Column("stripe_subscription_id", sa.Text()),
        sa.Column("current_period_end_utc", sa.Text()),
        sa.Column("usage_json", JSON_TEXT, nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_table("stripe_events", sa.Column("id", sa.Text(), primary_key=True), sa.Column("event_type", sa.Text(), nullable=False), sa.Column("processed_at_utc", sa.Text(), nullable=False), sa.Column("payload_json", JSON_TEXT, nullable=False))
    op.create_table("usage_events", sa.Column("id", sa.Text(), primary_key=True), sa.Column("organization_id", sa.Text(), nullable=False), sa.Column("user_id", sa.Text()), sa.Column("feature", sa.Text(), nullable=False), sa.Column("quantity", sa.Float(), nullable=False), sa.Column("properties_json", JSON_TEXT, nullable=False), sa.Column("occurred_at_utc", sa.Text(), nullable=False))
    op.create_table("audit_log", sa.Column("id", sa.Text(), primary_key=True), sa.Column("organization_id", sa.Text()), sa.Column("actor_user_id", sa.Text()), sa.Column("action", sa.Text(), nullable=False), sa.Column("target_type", sa.Text()), sa.Column("target_id", sa.Text()), sa.Column("metadata_json", JSON_TEXT, nullable=False), sa.Column("occurred_at_utc", sa.Text(), nullable=False))


def downgrade() -> None:
    for table in ("audit_log", "usage_events", "stripe_events", "subscriptions", "datasets", "jobs", "organization_members", "organizations", "auth_sessions", "users"):
        op.drop_table(table)
