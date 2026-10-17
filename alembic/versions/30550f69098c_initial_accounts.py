"""initial_accounts"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '30550f69098c'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if 'users' not in existing:
        op.create_table('users',
            sa.Column('id', sa.String(length=36), nullable=False), sa.Column('email', sa.String(length=320), nullable=False),
            sa.Column('password_hash', sa.String(length=256), nullable=False), sa.Column('nickname', sa.String(length=80), nullable=False),
            sa.Column('timezone', sa.String(length=64), nullable=False), sa.Column('topics', sa.JSON(), nullable=False), sa.Column('keywords', sa.JSON(), nullable=False),
            sa.Column('language', sa.String(length=16), nullable=False), sa.Column('style', sa.JSON(), nullable=False), sa.Column('daily_push_time', sa.String(length=5), nullable=True),
            sa.Column('settings_version', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint('id'))
        op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    if 'sessions' not in existing:
        op.create_table('sessions', sa.Column('token_hash', sa.String(length=64), nullable=False), sa.Column('user_id', sa.String(length=36), nullable=False), sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('token_hash'))
        op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    if 'briefings' not in existing:
        op.create_table('briefings', sa.Column('id', sa.String(length=36), nullable=False), sa.Column('user_id', sa.String(length=36), nullable=False), sa.Column('title', sa.String(length=200), nullable=False), sa.Column('query', sa.String(length=500), nullable=False), sa.Column('status', sa.String(length=32), nullable=False), sa.Column('html_path', sa.String(length=500), nullable=False), sa.Column('event_count', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'))
        op.create_index(op.f('ix_briefings_user_id'), 'briefings', ['user_id'], unique=False)
    if 'news_events' not in existing:
        op.create_table('news_events', sa.Column('id', sa.String(length=36), nullable=False), sa.Column('briefing_id', sa.String(length=36), nullable=False), sa.Column('user_id', sa.String(length=36), nullable=False), sa.Column('title', sa.String(length=500), nullable=False), sa.Column('source', sa.String(length=200), nullable=False), sa.Column('url', sa.String(length=2000), nullable=False), sa.Column('published_at', sa.DateTime(timezone=True), nullable=False), sa.Column('summary', sa.Text(), nullable=False), sa.Column('facts', sa.JSON(), nullable=False), sa.ForeignKeyConstraint(['briefing_id'], ['briefings.id'], ondelete='CASCADE'), sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'))
        op.create_index(op.f('ix_news_events_briefing_id'), 'news_events', ['briefing_id'], unique=False); op.create_index(op.f('ix_news_events_user_id'), 'news_events', ['user_id'], unique=False)
    # A database without an Alembic stamp may still contain application tables created by earlier dev builds.
def downgrade():
    op.drop_index(op.f('ix_generation_tasks_status'), table_name='generation_tasks')
    op.drop_index(op.f('ix_generation_tasks_user_id'), table_name='generation_tasks')
    op.drop_table('generation_tasks')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions'); op.drop_table('sessions')
    op.drop_index(op.f('ix_users_email'), table_name='users'); op.drop_table('users')
