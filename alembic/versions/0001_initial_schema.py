"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-07

Creates the nine core tables. Categorical columns are TEXT + CHECK rather than
Postgres ENUM, so adding a value later is an ALTER on the constraint instead of
a type migration.

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # jobs.embedding / resumes.embedding need the extension before those tables.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("canonical_domain", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("headcount_estimate", sa.Integer(), nullable=True),
        sa.Column("location_hq", sa.Text(), nullable=True),
        sa.Column("founded_year", sa.SmallInteger(), nullable=True),
        sa.Column("ats_provider", sa.Text(), nullable=True),
        sa.Column("ats_board_url", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IS NULL OR stage IN ('pre_seed', 'seed', 'series_a', "
            "'series_b', 'series_c', 'growth', 'public', 'bootstrapped', 'unknown')",
            name="ck_companies_stage",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_companies_slug", "companies", ["slug"], unique=True)
    op.create_index(
        "ix_companies_canonical_domain", "companies", ["canonical_domain"], unique=True
    )
    op.create_index(
        "ix_companies_aliases_gin", "companies", ["aliases"], postgresql_using="gin"
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("google_id", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), server_default="user", nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)

    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("fetch_tier", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "crawl_frequency_minutes",
            sa.Integer(),
            server_default="1440",
            nullable=False,
        ),
        sa.Column("next_crawl_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_crawl_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_successful_crawl_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("last_failure_reason", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "requires_js", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "reliability_score", sa.Float(), server_default="0.7", nullable=True
        ),
        sa.Column("total_crawls", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "total_events_extracted", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('career_page', 'engineering_blog', 'company_blog', "
            "'news_site', 'rss_feed', 'ats_api', 'github_org', 'news_api', "
            "'search_api')",
            name="ck_sources_source_type",
        ),
        sa.CheckConstraint(
            "fetch_tier IN ('ats_api', 'rss', 'static_http', 'playwright', "
            "'news_api', 'search_api')",
            name="ck_sources_fetch_tier",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'disabled')",
            name="ck_sources_status",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    # The scheduler query. Partial so rejected/disabled rows stay out of the index.
    op.create_index(
        "ix_sources_due",
        "sources",
        ["status", "next_crawl_at"],
        postgresql_where=sa.text("status = 'approved'"),
    )
    op.create_index("ix_sources_company_id", "sources", ["company_id"])

    op.create_table(
        "company_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("score_version", sa.Text(), nullable=False),
        sa.Column("momentum_score", sa.Float(), nullable=False),
        sa.Column("momentum_label", sa.Text(), nullable=False),
        sa.Column("signal_strength", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("confidence_components", postgresql.JSONB(), nullable=True),
        sa.Column(
            "contributing_event_ids", postgresql.ARRAY(sa.Uuid()), nullable=True
        ),
        sa.Column("score_delta", sa.Float(), nullable=True),
        sa.Column(
            "scored_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "momentum_label IN ('none', 'low', 'moderate', 'high', 'very_high')",
            name="ck_company_scores_momentum_label",
        ),
        sa.CheckConstraint(
            "momentum_score >= 0 AND momentum_score <= 100",
            name="ck_company_scores_momentum_range",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_scores_company_scored",
        "company_scores",
        ["company_id", "scored_at"],
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("structured_data", postgresql.JSONB(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("extraction_model", sa.Text(), nullable=True),
        sa.Column("extraction_prompt_version", sa.Text(), nullable=True),
        sa.Column("canonical_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "is_canonical", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('funding', 'new_office', 'leadership_change', "
            "'product_launch', 'engineering_expansion', 'ai_division', "
            "'infrastructure_investment', 'acquisition', 'partnership', "
            "'layoff', 'career_page_update')",
            name="ck_events_event_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'merged', 'retracted')", name="ck_events_status"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["canonical_event_id"], ["events.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_events_company_occurred",
        "events",
        ["company_id", "event_occurred_at"],
        postgresql_where=sa.text("is_canonical AND status = 'active'"),
    )
    op.create_index(
        "ix_events_type_occurred", "events", ["event_type", "event_occurred_at"]
    )
    op.create_index("ix_events_observed_at", "events", ["observed_at"])

    op.create_table(
        "crawl_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("content_changed", sa.Boolean(), nullable=True),
        sa.Column("cleaned_text", sa.Text(), nullable=True),
        sa.Column("http_status_code", sa.SmallInteger(), nullable=True),
        sa.Column(
            "events_extracted", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failure', 'skipped_unchanged', 'rate_limited')",
            name="ck_crawl_logs_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_crawl_logs_source_created", "crawl_logs", ["source_id", "created_at"]
    )
    op.create_index("ix_crawl_logs_created_at", "crawl_logs", ["created_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("role_family", sa.Text(), nullable=True),
        sa.Column("seniority", sa.Text(), nullable=True),
        sa.Column("employment_type", sa.Text(), nullable=True),
        sa.Column("work_mode", sa.Text(), nullable=True),
        sa.Column("location_raw", sa.Text(), nullable=True),
        sa.Column("location_normalized", sa.Text(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.Text(), nullable=True),
        sa.Column("skills", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("application_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(768), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role_family IS NULL OR role_family IN ('engineering', 'product', "
            "'design', 'data', 'devops', 'marketing', 'sales', 'operations', "
            "'hr', 'finance', 'legal', 'other')",
            name="ck_jobs_role_family",
        ),
        sa.CheckConstraint(
            "seniority IS NULL OR seniority IN ('intern', 'junior', 'mid', "
            "'senior', 'staff', 'principal', 'director', 'vp', 'c_level')",
            name="ck_jobs_seniority",
        ),
        sa.CheckConstraint(
            "employment_type IS NULL OR employment_type IN ('full_time', "
            "'part_time', 'contract', 'internship')",
            name="ck_jobs_employment_type",
        ),
        sa.CheckConstraint(
            "work_mode IS NULL OR work_mode IN ('remote', 'hybrid', 'onsite')",
            name="ck_jobs_work_mode",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_jobs_company_external",
        "jobs",
        ["company_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index("ix_jobs_active_role_family", "jobs", ["is_active", "role_family"])
    op.create_index("ix_jobs_skills_gin", "jobs", ["skills"], postgresql_using="gin")
    op.execute(
        "CREATE INDEX ix_jobs_fts ON jobs USING gin "
        "(to_tsvector('english', title || ' ' || coalesce(description_text, '')))"
    )

    op.create_table(
        "newsletter_subscribers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_newsletter_subscribers_email",
        "newsletter_subscribers",
        ["email"],
        unique=True,
    )

    op.create_table(
        "resumes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("file_hash", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("parsed_skills", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "parsed_role_families", postgresql.ARRAY(sa.Text()), nullable=True
        ),
        sa.Column("parsed_seniority", sa.Text(), nullable=True),
        sa.Column("parsed_experience_years", sa.Float(), nullable=True),
        sa.Column("parsed_locations", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("work_mode_preference", sa.Text(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(768), nullable=True),
        sa.Column("extraction_model", sa.Text(), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "work_mode_preference IS NULL OR work_mode_preference IN "
            "('remote', 'hybrid', 'onsite')",
            name="ck_resumes_work_mode_preference",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("resumes")
    op.drop_index("ix_newsletter_subscribers_email", table_name="newsletter_subscribers")
    op.drop_table("newsletter_subscribers")

    op.execute("DROP INDEX IF EXISTS ix_jobs_fts")
    op.drop_index("ix_jobs_skills_gin", table_name="jobs")
    op.drop_index("ix_jobs_active_role_family", table_name="jobs")
    op.drop_index("uq_jobs_company_external", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_crawl_logs_created_at", table_name="crawl_logs")
    op.drop_index("ix_crawl_logs_source_created", table_name="crawl_logs")
    op.drop_table("crawl_logs")

    op.drop_index("ix_events_observed_at", table_name="events")
    op.drop_index("ix_events_type_occurred", table_name="events")
    op.drop_index("ix_events_company_occurred", table_name="events")
    op.drop_table("events")

    op.drop_index("ix_company_scores_company_scored", table_name="company_scores")
    op.drop_table("company_scores")

    op.drop_index("ix_sources_company_id", table_name="sources")
    op.drop_index("ix_sources_due", table_name="sources")
    op.drop_table("sources")

    op.drop_index("ix_users_google_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_companies_aliases_gin", table_name="companies")
    op.drop_index("ix_companies_canonical_domain", table_name="companies")
    op.drop_index("ix_companies_slug", table_name="companies")
    op.drop_table("companies")
