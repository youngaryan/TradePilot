from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_paper_deployments_runs"
down_revision = "0005_durable_job_claims"
branch_labels = None
depends_on = None


JSON_TEXT = sa.Text()


def upgrade() -> None:
    op.create_table(
        "paper_deployments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Text(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_json", JSON_TEXT, nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("legacy_config_id", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.UniqueConstraint("organization_id", "id", name="uq_paper_deployments_org_id"),
    )
    op.create_index(
        "idx_paper_deployments_org_idempotency",
        "paper_deployments",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "idx_paper_deployments_org_legacy_config",
        "paper_deployments",
        ["organization_id", "legacy_config_id"],
        unique=True,
        postgresql_where=sa.text("legacy_config_id IS NOT NULL"),
        sqlite_where=sa.text("legacy_config_id IS NOT NULL"),
    )
    op.create_index(
        "idx_paper_deployments_org_status_updated",
        "paper_deployments",
        ["organization_id", "status", "updated_at_utc"],
    )

    op.add_column("paper_agents", sa.Column("deployment_id", sa.Text(), nullable=True))
    op.create_index(
        "idx_paper_agents_org_deployment_name",
        "paper_agents",
        ["organization_id", "deployment_id", "name"],
        unique=True,
        postgresql_where=sa.text("deployment_id IS NOT NULL"),
        sqlite_where=sa.text("deployment_id IS NOT NULL"),
    )
    op.create_index(
        "idx_paper_agents_org_deployment_updated",
        "paper_agents",
        ["organization_id", "deployment_id", "updated_at_utc"],
    )

    op.create_table(
        "paper_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployment_id", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("deployment_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("asof_date", sa.Text(), nullable=True),
        sa.Column("run_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_json", JSON_TEXT, nullable=False),
        sa.Column("deployment_config_json", JSON_TEXT, nullable=False),
        sa.Column("batch_summary_json", JSON_TEXT, nullable=False, server_default="{}"),
        sa.Column("aggregate_payload_json", JSON_TEXT, nullable=False, server_default="{}"),
        sa.Column("artifact_id", sa.Text(), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.Column("started_at_utc", sa.Text(), nullable=True),
        sa.Column("completed_at_utc", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "deployment_id"],
            ["paper_deployments.organization_id", "paper_deployments.id"],
            name="fk_paper_runs_org_deployment",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "deployment_id",
            "idempotency_key",
            name="uq_paper_runs_org_deployment_idempotency",
        ),
    )
    op.create_index(
        "idx_paper_runs_org_deployment_created",
        "paper_runs",
        ["organization_id", "deployment_id", "created_at_utc"],
    )
    op.create_index(
        "idx_paper_runs_org_status_updated",
        "paper_runs",
        ["organization_id", "status", "updated_at_utc"],
    )
    op.create_index(
        "idx_paper_runs_org_job_asof",
        "paper_runs",
        ["organization_id", "job_id", "asof_date", "run_index"],
    )


def downgrade() -> None:
    op.drop_index("idx_paper_runs_org_job_asof", table_name="paper_runs")
    op.drop_index("idx_paper_runs_org_status_updated", table_name="paper_runs")
    op.drop_index("idx_paper_runs_org_deployment_created", table_name="paper_runs")
    op.drop_table("paper_runs")
    op.drop_index("idx_paper_agents_org_deployment_updated", table_name="paper_agents")
    op.drop_index("idx_paper_agents_org_deployment_name", table_name="paper_agents")
    op.drop_column("paper_agents", "deployment_id")
    op.drop_index("idx_paper_deployments_org_status_updated", table_name="paper_deployments")
    op.drop_index("idx_paper_deployments_org_legacy_config", table_name="paper_deployments")
    op.drop_index("idx_paper_deployments_org_idempotency", table_name="paper_deployments")
    op.drop_table("paper_deployments")
