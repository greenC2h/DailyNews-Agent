-- DailyNews PostgreSQL 业务建表快照
-- 对应 Alembic revision: c3e8f1a4b209
-- 仅用于全新空数据库；已有数据库必须使用 uv run alembic upgrade head。
-- 不包含 LangGraph Checkpointer / Store、Milvus 或 alembic_version。
-- 不包含业务数据；UUID、时间和部分默认值由应用生成，非数据库默认值。
-- 在仓库根目录执行：psql -h localhost -U postgres -d dailynews -v ON_ERROR_STOP=1 -f database/schema.sql
-- 执行成功后，在同一数据库配置下运行：
--   uv run alembic stamp c3e8f1a4b209
-- stamp 仅登记已导入的结构版本，不执行建表；禁止对不匹配的已有库盲目 stamp。
-- 常规部署仍优先使用 Alembic；不要同时导入本文件和执行从零迁移。
-- 框架表由应用首次启动自行初始化。
-- 表说明：users 用户与偏好；sessions 登录会话；briefings 简报；
-- news_events 新闻事件；generation_tasks 生成任务；scheduled_tasks 定时规则；
-- mail_deliveries 邮件记录；task_executions 执行记录；
-- conversation_messages 业务对话消息（不属于 LangGraph 检查点表）。

BEGIN;

CREATE TABLE users (
	id VARCHAR(36) NOT NULL,
	email VARCHAR(320) NOT NULL,
	password_hash VARCHAR(256) NOT NULL,
	nickname VARCHAR(80) NOT NULL,
	timezone VARCHAR(64) NOT NULL,
	topics JSON NOT NULL,
	keywords JSON NOT NULL,
	language VARCHAR(16) NOT NULL,
	style JSON NOT NULL,
	daily_push_time VARCHAR(5),
	settings_version INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT users_pkey PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE briefings (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	title VARCHAR(200) NOT NULL,
	query VARCHAR(500) NOT NULL,
	status VARCHAR(32) NOT NULL,
	html_path VARCHAR(500) NOT NULL,
	event_count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	read_at TIMESTAMP WITH TIME ZONE,
	event_ids JSON DEFAULT '[]'::json NOT NULL,
	history_query JSON DEFAULT '{}'::json NOT NULL,
	html_size BIGINT,
	html_sha256 VARCHAR(64),
	CONSTRAINT briefings_pkey PRIMARY KEY (id),
	CONSTRAINT briefings_user_id_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_briefings_user_id ON briefings (user_id);

CREATE TABLE conversation_messages (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	thread_id VARCHAR(100) NOT NULL,
	role VARCHAR(20) NOT NULL,
	content TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT conversation_messages_pkey PRIMARY KEY (id),
	CONSTRAINT conversation_messages_user_id_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_conversation_messages_thread_id ON conversation_messages (thread_id);

CREATE INDEX ix_conversation_messages_user_id ON conversation_messages (user_id);

CREATE TABLE scheduled_tasks (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	name VARCHAR(200) NOT NULL,
	frequency VARCHAR(16) NOT NULL,
	timezone VARCHAR(64) NOT NULL,
	run_at TIMESTAMP WITH TIME ZONE NOT NULL,
	query VARCHAR(500) NOT NULL,
	"limit" INTEGER NOT NULL,
	recipient_email VARCHAR(320) NOT NULL,
	status VARCHAR(20) NOT NULL,
	next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_run_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	is_default BOOLEAN DEFAULT false NOT NULL,
	window_start TIMESTAMP WITH TIME ZONE,
	window_end TIMESTAMP WITH TIME ZONE,
	overrides JSON DEFAULT '{}'::json NOT NULL,
	idempotency_key VARCHAR(200),
	CONSTRAINT scheduled_tasks_pkey PRIMARY KEY (id),
	CONSTRAINT scheduled_tasks_user_id_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_scheduled_tasks_status ON scheduled_tasks (status);

CREATE INDEX ix_scheduled_tasks_user_id ON scheduled_tasks (user_id);

CREATE UNIQUE INDEX uq_default_schedule ON scheduled_tasks (user_id) WHERE (is_default = true);

CREATE UNIQUE INDEX uq_schedule_idempotency ON scheduled_tasks (user_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);

CREATE TABLE sessions (
	token_hash VARCHAR(64) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT sessions_pkey PRIMARY KEY (token_hash),
	CONSTRAINT sessions_user_id_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE TABLE generation_tasks (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	query VARCHAR(500) NOT NULL,
	"limit" INTEGER NOT NULL,
	window_start TIMESTAMP WITH TIME ZONE NOT NULL,
	window_end TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(32) NOT NULL,
	briefing_id VARCHAR(36),
	error_code VARCHAR(64),
	cancel_requested BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	scheduled_task_id VARCHAR(36),
	planned_at TIMESTAMP WITH TIME ZONE,
	snapshot JSON DEFAULT '{}'::json NOT NULL,
	idempotency_key VARCHAR(200),
	lease_owner VARCHAR(36),
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	recovery_count INTEGER DEFAULT 0 NOT NULL,
	retry_count INTEGER DEFAULT 0 NOT NULL,
	intermediate JSON,
	intermediate_expires_at TIMESTAMP WITH TIME ZONE,
	send_email BOOLEAN DEFAULT false NOT NULL,
	CONSTRAINT generation_tasks_pkey PRIMARY KEY (id),
	CONSTRAINT fk_generation_schedule FOREIGN KEY(scheduled_task_id) REFERENCES scheduled_tasks (id),
	CONSTRAINT generation_tasks_briefing_id_fkey FOREIGN KEY(briefing_id) REFERENCES briefings (id) ON DELETE SET NULL,
	CONSTRAINT generation_tasks_user_id_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_generation_tasks_scheduled_task_id ON generation_tasks (scheduled_task_id);

CREATE INDEX ix_generation_tasks_status ON generation_tasks (status);

CREATE INDEX ix_generation_tasks_user_id ON generation_tasks (user_id);

CREATE UNIQUE INDEX uq_generation_idempotency ON generation_tasks (user_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);

CREATE UNIQUE INDEX uq_planned_execution ON generation_tasks (scheduled_task_id, planned_at) WHERE (scheduled_task_id IS NOT NULL);

CREATE TABLE mail_deliveries (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	briefing_id VARCHAR(36) NOT NULL,
	recipient_email VARCHAR(320) NOT NULL,
	status VARCHAR(32) NOT NULL,
	attempts INTEGER NOT NULL,
	last_error VARCHAR(500),
	sent_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	original_id VARCHAR(36),
	CONSTRAINT mail_deliveries_pkey PRIMARY KEY (id),
	CONSTRAINT mail_deliveries_briefing_id_fkey FOREIGN KEY(briefing_id) REFERENCES briefings (id) ON DELETE CASCADE,
	CONSTRAINT mail_deliveries_user_id_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_mail_deliveries_briefing_id ON mail_deliveries (briefing_id);

CREATE INDEX ix_mail_deliveries_user_id ON mail_deliveries (user_id);

CREATE TABLE news_events (
	id VARCHAR(36) NOT NULL,
	briefing_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	title VARCHAR(500) NOT NULL,
	source VARCHAR(200) NOT NULL,
	url VARCHAR(2000) NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE NOT NULL,
	summary TEXT NOT NULL,
	facts JSON NOT NULL,
	index_status VARCHAR(20) DEFAULT 'pending'::character varying NOT NULL,
	index_error VARCHAR(100),
	search_text TEXT DEFAULT ''::text NOT NULL,
	index_retry_count INTEGER DEFAULT 0 NOT NULL,
	CONSTRAINT news_events_pkey PRIMARY KEY (id),
	CONSTRAINT news_events_briefing_id_fkey FOREIGN KEY(briefing_id) REFERENCES briefings (id) ON DELETE CASCADE,
	CONSTRAINT news_events_user_id_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_news_events_briefing_id ON news_events (briefing_id);

CREATE INDEX ix_news_events_user_id ON news_events (user_id);

CREATE TABLE task_executions (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	generation_task_id VARCHAR(36) NOT NULL,
	status VARCHAR(32) NOT NULL,
	phase VARCHAR(32) NOT NULL,
	error_code VARCHAR(64),
	event_count INTEGER NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT task_executions_pkey PRIMARY KEY (id),
	CONSTRAINT task_executions_generation_task_id_fkey FOREIGN KEY(generation_task_id) REFERENCES generation_tasks (id) ON DELETE CASCADE,
	CONSTRAINT task_executions_user_id_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_task_executions_generation_task_id ON task_executions (generation_task_id);

CREATE INDEX ix_task_executions_user_id ON task_executions (user_id);

COMMIT;

