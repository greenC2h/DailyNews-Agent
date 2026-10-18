"""为简报 HTML 保存相对路径完整性元数据。"""
from alembic import op
import sqlalchemy as sa


revision = "c3e8f1a4b209"
down_revision = "b76f52a17401"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加可空元数据字段，保留现有绝对路径记录的可读性。"""
    op.add_column("briefings", sa.Column("html_size", sa.BigInteger(), nullable=True))
    op.add_column("briefings", sa.Column("html_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """禁止自动丢弃已保存完整性元数据，要求保留向前兼容数据。"""
    raise RuntimeError("请保留 HTML 完整性元数据并回滚应用代码；不自动删除历史字段")
