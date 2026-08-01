from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_durable_job_claims"
down_revision = "0004_market_research_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("jobs", sa.Column("worker_id", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("heartbeat_at_utc", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("lease_expires_at_utc", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("rq_job_id", sa.Text(), nullable=True))
    op.create_index("idx_jobs_status_lease", "jobs", ["status", "lease_expires_at_utc"])
    op.create_index(
        "idx_jobs_rq_job_id_unique",
        "jobs",
        ["rq_job_id"],
        unique=True,
        postgresql_where=sa.text("rq_job_id IS NOT NULL"),
        sqlite_where=sa.text("rq_job_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_jobs_rq_job_id_unique", table_name="jobs")
    op.drop_index("idx_jobs_status_lease", table_name="jobs")
    op.drop_column("jobs", "rq_job_id")
    op.drop_column("jobs", "lease_expires_at_utc")
    op.drop_column("jobs", "heartbeat_at_utc")
    op.drop_column("jobs", "worker_id")
    op.drop_column("jobs", "max_attempts")
    op.drop_column("jobs", "attempt")
    op.drop_column("jobs", "version")
