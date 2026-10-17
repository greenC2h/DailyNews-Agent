"""历史专题关联与任务幂等性。"""
from alembic import op
import sqlalchemy as sa
revision = 'b76f52a17401'
down_revision = '7e539bd9a8a5'
branch_labels = None
depends_on = None

def upgrade():
    """补充历史事件搜索资料及非破坏性关联字段。"""
    op.add_column('briefings', sa.Column('event_ids', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('briefings', sa.Column('history_query', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('news_events', sa.Column('search_text', sa.Text(), nullable=False, server_default=''))
    op.add_column('news_events', sa.Column('index_retry_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('scheduled_tasks', sa.Column('idempotency_key', sa.String(200), nullable=True))
    op.create_index('uq_generation_idempotency', 'generation_tasks', ['user_id', 'idempotency_key'], unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'))
    op.create_index('uq_schedule_idempotency', 'scheduled_tasks', ['user_id', 'idempotency_key'], unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'))
    op.create_index('uq_default_schedule', 'scheduled_tasks', ['user_id'], unique=True, postgresql_where=sa.text('is_default = true'))
    op.create_index('uq_planned_execution', 'generation_tasks', ['scheduled_task_id', 'planned_at'], unique=True, postgresql_where=sa.text('scheduled_task_id IS NOT NULL'))

def downgrade():
    """保留历史数据，不自动删除已保存关联。"""
    raise RuntimeError('请回滚应用代码并保留向前兼容字段')
