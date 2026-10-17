"""scheduled tasks and mail deliveries"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision: str = "4b2e5c91c6d0"
down_revision: Union[str, None] = "8bfa62f31f2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade():
    """Create scheduled task and mail delivery tables."""
    op.create_table("scheduled_tasks", sa.Column("id",sa.String(36),primary_key=True), sa.Column("user_id",sa.String(36),nullable=False), sa.Column("name",sa.String(200),nullable=False), sa.Column("frequency",sa.String(16),nullable=False), sa.Column("timezone",sa.String(64),nullable=False), sa.Column("run_at",sa.DateTime(timezone=True),nullable=False), sa.Column("query",sa.String(500),nullable=False), sa.Column("limit",sa.Integer(),nullable=False), sa.Column("recipient_email",sa.String(320),nullable=False), sa.Column("status",sa.String(20),nullable=False), sa.Column("next_run_at",sa.DateTime(timezone=True),nullable=False), sa.Column("last_run_at",sa.DateTime(timezone=True),nullable=True), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.ForeignKeyConstraint(["user_id"],["users.id"],ondelete="CASCADE"))
    op.create_index("ix_scheduled_tasks_user_id","scheduled_tasks",["user_id"]); op.create_index("ix_scheduled_tasks_status","scheduled_tasks",["status"])
    op.create_table("mail_deliveries", sa.Column("id",sa.String(36),primary_key=True), sa.Column("user_id",sa.String(36),nullable=False), sa.Column("briefing_id",sa.String(36),nullable=False), sa.Column("recipient_email",sa.String(320),nullable=False), sa.Column("status",sa.String(32),nullable=False), sa.Column("attempts",sa.Integer(),nullable=False), sa.Column("last_error",sa.String(500),nullable=True), sa.Column("sent_at",sa.DateTime(timezone=True),nullable=True), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.ForeignKeyConstraint(["user_id"],["users.id"],ondelete="CASCADE"), sa.ForeignKeyConstraint(["briefing_id"],["briefings.id"],ondelete="CASCADE"))
    op.create_index("ix_mail_deliveries_user_id","mail_deliveries",["user_id"]); op.create_index("ix_mail_deliveries_briefing_id","mail_deliveries",["briefing_id"])
def downgrade():
    """Drop scheduled task and mail delivery tables."""
    op.drop_table("mail_deliveries"); op.drop_table("scheduled_tasks")
