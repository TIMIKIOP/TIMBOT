"""track embedding profiles and inference latency"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "8c31b7b02a14"
down_revision: Union[str, None] = "4af4ddc861a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("knowledge_chunks", sa.Column("embedding_model", sa.String(length=200), nullable=True))
    op.create_index(op.f("ix_knowledge_chunks_embedding_model"), "knowledge_chunks", ["embedding_model"], unique=False)
    op.add_column("ai_usage", sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"))
    if op.get_bind().dialect.name == "postgresql":
        # Widen the type modifier without dropping legacy vectors. Their model
        # marker stays NULL, so Ollama retrieval cannot mix them with new rows.
        op.alter_column(
            "knowledge_chunks", "embedding", existing_type=Vector(1536), type_=Vector(),
            postgresql_using="embedding::vector",
        )

def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM knowledge_chunks WHERE embedding IS NOT NULL AND vector_dims(embedding) <> 1536) THEN
            RAISE EXCEPTION 'Cannot narrow embedding vectors: non-1536 embeddings exist';
          END IF;
        END $$""")
        op.alter_column(
            "knowledge_chunks", "embedding", existing_type=Vector(), type_=Vector(1536),
            postgresql_using="embedding::vector(1536)",
        )
    op.drop_column("ai_usage", "latency_ms")
    op.drop_index(op.f("ix_knowledge_chunks_embedding_model"), table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "embedding_model")
