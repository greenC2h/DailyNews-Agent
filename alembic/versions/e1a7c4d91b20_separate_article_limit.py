"""Separate acquired article caps from generated event caps."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1a7c4d91b20"
down_revision: Union[str, None] = "c3e8f1a4b209"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为任务新增独立文章采集上限，并将历史执行快照按原事件数两倍回填。"""
    op.add_column("scheduled_tasks", sa.Column("article_limit", sa.Integer(), nullable=True))
    op.add_column("generation_tasks", sa.Column("article_limit", sa.Integer(), nullable=True))
    op.execute("UPDATE generation_tasks SET article_limit = \"limit\" * 2 WHERE article_limit IS NULL")
    with op.batch_alter_table("generation_tasks") as batch:
        batch.alter_column("article_limit", nullable=False)


def downgrade() -> None:
    """保留已创建的执行快照，不自动删除任务采集边界。"""
    raise RuntimeError("请回滚应用代码并保留 article_limit 字段")
