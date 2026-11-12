"""add workspace scoped AI usage tracking"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "4af4ddc861a1"
down_revision: Union[str, None] = "08e7ddb7827d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=30), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_usage_workspace_id"), "ai_usage", ["workspace_id"], unique=False)

def downgrade() -> None:
    op.drop_index(op.f("ix_ai_usage_workspace_id"), table_name="ai_usage")
    op.drop_table("ai_usage")
