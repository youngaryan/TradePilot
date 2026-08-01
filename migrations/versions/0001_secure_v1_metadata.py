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
        sa.Column("email_verified_at_utc", sa.Text()),
        sa.Column("mfa_secret", sa.Text()),
        sa.Column("mfa_enabled", sa.Integer(), nullable=False, server_default="0"),
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
        "auth_tokens",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("expires_at_utc", sa.Text(), nullable=False),
        sa.Column("consumed_at_utc", sa.Text()),
    )
    op.create_index("idx_auth_tokens_user_purpose", "auth_tokens", ["user_id", "purpose", "created_at_utc"])
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
    op.create_index("idx_jobs_kind_status", "jobs", ["kind", "status"])
    op.create_table(
        "deployment_configs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("path", sa.Text()),
        sa.Column("config_json", JSON_TEXT, nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
    )
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("artifact_dir", sa.Text()),
        sa.Column("summary_json", JSON_TEXT, nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_experiment_runs_kind_created", "experiment_runs", ["kind", "created_at_utc"])
    op.create_table(
        "projects",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.UniqueConstraint("organization_id", "slug"),
    )
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
        "api_keys",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("masked_value", sa.Text(), nullable=False),
        sa.Column("secret_ref", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_api_keys_org_provider", "api_keys", ["organization_id", "provider"])
    op.create_table(
        "experiments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Text(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("pipeline", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("artifact_dir", sa.Text()),
        sa.Column("summary_json", JSON_TEXT, nullable=False),
        sa.Column("validation_json", JSON_TEXT, nullable=False),
        sa.Column("lineage_json", JSON_TEXT, nullable=False),
        sa.Column("readiness_json", JSON_TEXT, nullable=False),
        sa.Column("trades_json", JSON_TEXT, nullable=False),
        sa.Column("sentiment_json", JSON_TEXT, nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_experiments_org_created", "experiments", ["organization_id", "created_at_utc"])
    op.create_table(
        "paper_agents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Text(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("pipeline", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fake_cash", sa.Float(), nullable=False, server_default="0"),
        sa.Column("config_json", JSON_TEXT, nullable=False),
        sa.Column("latest_payload_json", JSON_TEXT, nullable=False),
        sa.Column("warnings_json", JSON_TEXT, nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_paper_agents_org_updated", "paper_agents", ["organization_id", "updated_at_utc"])
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
    op.create_table(
        "organization_quotas",
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("quotas_json", JSON_TEXT, nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_table(
        "telemetry_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text()),
        sa.Column("user_id", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("properties_json", JSON_TEXT, nullable=False),
        sa.Column("context_json", JSON_TEXT, nullable=False),
        sa.Column("consent", sa.Text(), nullable=False),
        sa.Column("occurred_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_telemetry_org_time", "telemetry_events", ["organization_id", "occurred_at_utc"])
    op.create_index("idx_telemetry_name_time", "telemetry_events", ["name", "occurred_at_utc"])
    op.create_table(
        "refresh_statuses",
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_success_at_utc", sa.Text()),
        sa.Column("last_attempt_at_utc", sa.Text()),
        sa.Column("next_due_at_utc", sa.Text(), nullable=False),
        sa.Column("latest_run_id", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_refresh_status_due", "refresh_statuses", ["next_due_at_utc", "status"])
    op.create_table(
        "refresh_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("started_at_utc", sa.Text()),
        sa.Column("finished_at_utc", sa.Text()),
        sa.Column("locked_until_utc", sa.Text()),
        sa.Column("summary_json", JSON_TEXT, nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_refresh_runs_user_created", "refresh_runs", ["user_id", "created_at_utc"])
    op.create_table("stripe_events", sa.Column("id", sa.Text(), primary_key=True), sa.Column("event_type", sa.Text(), nullable=False), sa.Column("processed_at_utc", sa.Text(), nullable=False), sa.Column("payload_json", JSON_TEXT, nullable=False))
    op.create_table("usage_events", sa.Column("id", sa.Text(), primary_key=True), sa.Column("organization_id", sa.Text(), nullable=False), sa.Column("user_id", sa.Text()), sa.Column("feature", sa.Text(), nullable=False), sa.Column("quantity", sa.Float(), nullable=False), sa.Column("properties_json", JSON_TEXT, nullable=False), sa.Column("occurred_at_utc", sa.Text(), nullable=False))
    op.create_index("idx_usage_org_feature_time", "usage_events", ["organization_id", "feature", "occurred_at_utc"])
    op.create_table("audit_log", sa.Column("id", sa.Text(), primary_key=True), sa.Column("organization_id", sa.Text()), sa.Column("actor_user_id", sa.Text()), sa.Column("action", sa.Text(), nullable=False), sa.Column("target_type", sa.Text()), sa.Column("target_id", sa.Text()), sa.Column("metadata_json", JSON_TEXT, nullable=False), sa.Column("occurred_at_utc", sa.Text(), nullable=False))
    op.create_index("idx_audit_org_time", "audit_log", ["organization_id", "occurred_at_utc"])


def downgrade() -> None:
    for table in (
        "audit_log",
        "usage_events",
        "stripe_events",
        "refresh_runs",
        "refresh_statuses",
        "telemetry_events",
        "organization_quotas",
        "subscriptions",
        "paper_agents",
        "experiments",
        "api_keys",
        "datasets",
        "projects",
        "experiment_runs",
        "deployment_configs",
        "jobs",
        "organization_members",
        "organizations",
        "auth_tokens",
        "auth_sessions",
        "users",
    ):
        op.drop_table(table)
