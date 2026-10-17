"""增加任务租约、执行快照及索引和邮件恢复字段，保留历史数据。"""
from alembic import op
import sqlalchemy as sa
revision = "7e539bd9a8a5"
down_revision = "9a17d5e4c0f1"
branch_labels = None
depends_on = None

def upgrade():
    """以有默认值的增量字段升级现有数据库。"""
    op.add_column('briefings', sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('generation_tasks', sa.Column('scheduled_task_id', sa.String(length=36), nullable=True))
    op.add_column('generation_tasks', sa.Column('planned_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('generation_tasks', sa.Column('snapshot', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column('generation_tasks', sa.Column('idempotency_key', sa.String(length=200), nullable=True))
    op.add_column('generation_tasks', sa.Column('lease_owner', sa.String(length=36), nullable=True))
    op.add_column('generation_tasks', sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('generation_tasks', sa.Column('recovery_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('generation_tasks', sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('generation_tasks', sa.Column('intermediate', sa.JSON(), nullable=True))
    op.add_column('generation_tasks', sa.Column('intermediate_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('generation_tasks', sa.Column('send_email', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(op.f('ix_generation_tasks_scheduled_task_id'), 'generation_tasks', ['scheduled_task_id'], unique=False)
    op.create_foreign_key('fk_generation_schedule', 'generation_tasks', 'scheduled_tasks', ['scheduled_task_id'], ['id'])
    op.add_column('mail_deliveries', sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('mail_deliveries', sa.Column('original_id', sa.String(length=36), nullable=True))
    op.add_column('news_events', sa.Column('index_status', sa.String(length=20), nullable=False, server_default='pending'))
    op.add_column('news_events', sa.Column('index_error', sa.String(length=100), nullable=True))
    op.add_column('scheduled_tasks', sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('scheduled_tasks', sa.Column('window_start', sa.DateTime(timezone=True), nullable=True))
    op.add_column('scheduled_tasks', sa.Column('window_end', sa.DateTime(timezone=True), nullable=True))
    op.add_column('scheduled_tasks', sa.Column('overrides', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    """禁止自动删除执行资料，回滚应用时保留向前兼容字段。"""
    raise RuntimeError("请保留数据并回滚应用代码；不自动删除恢复记录")
