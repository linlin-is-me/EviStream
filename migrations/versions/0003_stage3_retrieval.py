"""Stage 3 hybrid retrieval indexes and embedding metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_stage3_retrieval"
down_revision: str | None = "0002_stage2_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("search_documents", sa.Column("normalized_text", sa.Text(), nullable=True))
    op.add_column("search_documents", sa.Column("keyword_lexemes", sa.Text(), nullable=True))
    op.execute(
        "UPDATE search_documents SET normalized_text = "
        "lower(regexp_replace(text, '\\s+', ' ', 'g')), "
        "keyword_lexemes = lower(regexp_replace(text, '\\s+', ' ', 'g'))"
    )
    op.alter_column("search_documents", "normalized_text", nullable=False)
    op.alter_column("search_documents", "keyword_lexemes", nullable=False)
    op.add_column(
        "search_documents",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', keyword_lexemes)", persisted=True),
            nullable=False,
        ),
    )
    op.add_column("search_documents", sa.Column("embedding_space", sa.String(64)))
    op.add_column("search_documents", sa.Column("embedding_model", sa.String(255)))
    op.add_column("search_documents", sa.Column("embedding_source_sha256", sa.String(64)))
    op.add_column(
        "search_documents", sa.Column("embedding_updated_at", sa.DateTime(timezone=True))
    )
    op.drop_constraint("search_documents_check", "search_documents", type_="check")
    op.create_check_constraint(
        "ck_search_documents_time",
        "search_documents",
        "start_ms >= 0 AND end_ms > start_ms",
    )
    op.create_index(
        "ix_search_documents_search_vector",
        "search_documents",
        ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_search_documents_embedding_space",
        "search_documents",
        ["embedding_space"],
    )
    op.execute(
        "CREATE INDEX ix_search_documents_embedding_hnsw ON search_documents "
        "USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_search_documents_embedding_hnsw", table_name="search_documents")
    op.drop_index("ix_search_documents_embedding_space", table_name="search_documents")
    op.drop_index("ix_search_documents_search_vector", table_name="search_documents")
    op.drop_constraint("ck_search_documents_time", "search_documents", type_="check")
    op.create_check_constraint(
        "search_documents_check",
        "search_documents",
        "start_ms >= 0 AND end_ms >= start_ms",
    )
    for column in [
        "embedding_updated_at",
        "embedding_source_sha256",
        "embedding_model",
        "embedding_space",
        "search_vector",
        "keyword_lexemes",
        "normalized_text",
    ]:
        op.drop_column("search_documents", column)
