from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_market_research_reports"
down_revision = "0003_user_strategies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_research_reports",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("parent_report_id", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("analysis_date", sa.Text(), nullable=False),
        sa.Column("horizon", sa.Text(), nullable=False),
        sa.Column("report_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("source_references_json", sa.Text(), nullable=False),
        sa.Column("provider_metadata_json", sa.Text(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.Column("completed_at_utc", sa.Text(), nullable=True),
        sa.Column("deleted_at_utc", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_market_research_reports_user_created",
        "market_research_reports",
        ["organization_id", "user_id", "created_at_utc"],
    )
    op.create_index(
        "idx_market_research_reports_ticker_created",
        "market_research_reports",
        ["organization_id", "user_id", "ticker", "created_at_utc"],
    )
    op.create_index(
        "idx_market_research_reports_status_created",
        "market_research_reports",
        ["organization_id", "user_id", "status", "created_at_utc"],
    )
    op.create_index(
        "idx_market_research_reports_job",
        "market_research_reports",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
        sqlite_where=sa.text("job_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_market_research_reports_job", table_name="market_research_reports")
    op.drop_index("idx_market_research_reports_status_created", table_name="market_research_reports")
    op.drop_index("idx_market_research_reports_ticker_created", table_name="market_research_reports")
    op.drop_index("idx_market_research_reports_user_created", table_name="market_research_reports")
    op.drop_table("market_research_reports")
