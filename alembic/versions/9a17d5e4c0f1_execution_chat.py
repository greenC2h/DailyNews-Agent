"""execution records and conversation messages"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision: str = "9a17d5e4c0f1"
down_revision: Union[str, None] = "4b2e5c91c6d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade():
    """Create execution and conversation tables."""
    op.create_table("task_executions", sa.Column("id",sa.String(36),primary_key=True), sa.Column("user_id",sa.String(36),nullable=False), sa.Column("generation_task_id",sa.String(36),nullable=False), sa.Column("status",sa.String(32),nullable=False), sa.Column("phase",sa.String(32),nullable=False), sa.Column("error_code",sa.String(64)), sa.Column("event_count",sa.Integer(),nullable=False), sa.Column("started_at",sa.DateTime(timezone=True),nullable=False), sa.Column("finished_at",sa.DateTime(timezone=True)), sa.ForeignKeyConstraint(["user_id"],["users.id"],ondelete="CASCADE"), sa.ForeignKeyConstraint(["generation_task_id"],["generation_tasks.id"],ondelete="CASCADE")); op.create_index("ix_task_executions_user_id","task_executions",["user_id"]); op.create_index("ix_task_executions_generation_task_id","task_executions",["generation_task_id"])
    op.create_table("conversation_messages", sa.Column("id",sa.String(36),primary_key=True), sa.Column("user_id",sa.String(36),nullable=False), sa.Column("thread_id",sa.String(100),nullable=False), sa.Column("role",sa.String(20),nullable=False), sa.Column("content",sa.Text(),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.ForeignKeyConstraint(["user_id"],["users.id"],ondelete="CASCADE")); op.create_index("ix_conversation_messages_user_id","conversation_messages",["user_id"]); op.create_index("ix_conversation_messages_thread_id","conversation_messages",["thread_id"])
def downgrade():
    """Drop execution and conversation tables."""
    op.drop_table("conversation_messages"); op.drop_table("task_executions")
