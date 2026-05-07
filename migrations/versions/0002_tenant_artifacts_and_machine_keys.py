from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_tenant_artifacts"
down_revision = "0001_secure_v1"
branch_labels = None
depends_on = None


JSON_TEXT = sa.Text()


def upgrade() -> None:
    op.add_column("jobs", sa.Column("organization_id", sa.Text()))
    op.add_column("deployment_configs", sa.Column("organization_id", sa.Text()))
    op.add_column("experiment_runs", sa.Column("organization_id", sa.Text()))
    op.add_column("api_keys", sa.Column("token_hash", sa.Text()))
    op.add_column("api_keys", sa.Column("scopes_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("api_keys", sa.Column("last_used_at_utc", sa.Text()))

    op.create_index("idx_jobs_org_kind_created", "jobs", ["organization_id", "kind", "created_at_utc"])
    op.create_index("idx_jobs_org_kind_status", "jobs", ["organization_id", "kind", "status"])
    op.create_index("idx_experiment_runs_org_kind_created", "experiment_runs", ["organization_id", "kind", "created_at_utc"])
    op.create_index("idx_api_keys_token_hash", "api_keys", ["token_hash"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text()),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("byte_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", JSON_TEXT, nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
    )
    op.create_index("idx_artifacts_org_type_updated", "artifacts", ["organization_id", "artifact_type", "updated_at_utc"])


def downgrade() -> None:
    op.drop_index("idx_artifacts_org_type_updated", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("idx_api_keys_token_hash", table_name="api_keys")
    op.drop_index("idx_experiment_runs_org_kind_created", table_name="experiment_runs")
    op.drop_index("idx_jobs_org_kind_status", table_name="jobs")
    op.drop_index("idx_jobs_org_kind_created", table_name="jobs")
    op.drop_column("api_keys", "last_used_at_utc")
    op.drop_column("api_keys", "scopes_json")
    op.drop_column("api_keys", "token_hash")
    op.drop_column("experiment_runs", "organization_id")
    op.drop_column("deployment_configs", "organization_id")
    op.drop_column("jobs", "organization_id")
