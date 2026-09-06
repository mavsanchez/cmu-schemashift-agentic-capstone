CREATE TABLE IF NOT EXISTS conversations (
    conversation_id UUID PRIMARY KEY,
    title TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT fk_sessions_conversation
        FOREIGN KEY (conversation_id)
        REFERENCES conversations (conversation_id)
        ON DELETE CASCADE,
    CONSTRAINT uq_sessions_conversation
        UNIQUE (conversation_id, session_id),
    CONSTRAINT ck_sessions_time_order
        CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL,
    origin_session_id UUID NOT NULL,
    turn_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    revision_count INTEGER NOT NULL DEFAULT 0,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_detail TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    waiting_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_runs_conversation
        FOREIGN KEY (conversation_id)
        REFERENCES conversations (conversation_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_runs_origin_session
        FOREIGN KEY (conversation_id, origin_session_id)
        REFERENCES sessions (conversation_id, session_id),
    CONSTRAINT uq_runs_conversation
        UNIQUE (conversation_id, run_id),
    CONSTRAINT uq_runs_turn
        UNIQUE (conversation_id, run_id, turn_id),
    CONSTRAINT ck_runs_turn_matches_run
        CHECK (turn_id = run_id),
    CONSTRAINT ck_runs_status
        CHECK (status IN ('pending', 'active', 'waiting_human', 'completed', 'unresolved', 'failed')),
    CONSTRAINT ck_runs_revision_count
        CHECK (revision_count >= 0),
    CONSTRAINT ck_runs_terminal_time
        CHECK (
            (status IN ('completed', 'unresolved', 'failed') AND completed_at IS NOT NULL)
            OR (status NOT IN ('completed', 'unresolved', 'failed'))
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_one_open_per_conversation
    ON runs (conversation_id)
    WHERE status IN ('pending', 'active', 'waiting_human');

CREATE TABLE IF NOT EXISTS messages (
    message_id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL,
    session_id UUID NOT NULL,
    run_id UUID NOT NULL,
    turn_id UUID NOT NULL,
    actor_type TEXT NOT NULL,
    actor_name TEXT,
    role TEXT,
    content TEXT NOT NULL,
    tool_name TEXT,
    tool_call_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_messages_session
        FOREIGN KEY (conversation_id, session_id)
        REFERENCES sessions (conversation_id, session_id),
    CONSTRAINT fk_messages_run
        FOREIGN KEY (conversation_id, run_id, turn_id)
        REFERENCES runs (conversation_id, run_id, turn_id),
    CONSTRAINT ck_messages_turn_matches_run
        CHECK (turn_id = run_id),
    CONSTRAINT ck_messages_actor_type
        CHECK (actor_type IN ('user', 'agent', 'subagent', 'tool', 'system')),
    CONSTRAINT ck_messages_tool_fields
        CHECK (
            actor_type = 'tool'
            OR (tool_name IS NULL AND tool_call_id IS NULL)
        )
);

CREATE TABLE IF NOT EXISTS agent_events (
    event_id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL,
    session_id UUID NOT NULL,
    run_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    component TEXT,
    status TEXT,
    label TEXT,
    detail TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_agent_events_session
        FOREIGN KEY (conversation_id, session_id)
        REFERENCES sessions (conversation_id, session_id),
    CONSTRAINT fk_agent_events_run
        FOREIGN KEY (conversation_id, run_id)
        REFERENCES runs (conversation_id, run_id),
    CONSTRAINT ck_agent_events_component
        CHECK (
            component IS NULL
            OR component IN ('mcp', 'memory', 'vector', 'agent', 'subagent', 'model', 'guardrail')
        ),
    CONSTRAINT ck_agent_events_status
        CHECK (status IS NULL OR status IN ('idle', 'active', 'done', 'error'))
);

CREATE TABLE IF NOT EXISTS uploaded_sources (
    source_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL,
    session_id UUID NOT NULL,
    original_name TEXT NOT NULL,
    local_path TEXT NOT NULL,
    role TEXT,
    mime_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'staged',
    table_name TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_uploaded_sources_session
        FOREIGN KEY (conversation_id, session_id)
        REFERENCES sessions (conversation_id, session_id),
    CONSTRAINT uq_uploaded_source_content
        UNIQUE (conversation_id, session_id, sha256, original_name),
    CONSTRAINT uq_uploaded_source_path
        UNIQUE (local_path),
    CONSTRAINT ck_uploaded_sources_role
        CHECK (
            role IS NULL
            OR role IN ('old_schema', 'new_schema', 'source_sql', 'migration_knowledge', 'old_data', 'new_data')
        ),
    CONSTRAINT ck_uploaded_sources_status
        CHECK (status IN ('staged', 'confirmed', 'indexed', 'failed')),
    CONSTRAINT ck_uploaded_sources_size
        CHECK (size_bytes >= 0),
    CONSTRAINT ck_uploaded_sources_sha256
        CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS human_reviews (
    review_id UUID PRIMARY KEY,
    candidate_id UUID NOT NULL,
    conversation_id UUID NOT NULL,
    run_id UUID NOT NULL,
    requested_session_id UUID NOT NULL,
    decision_session_id UUID,
    title TEXT NOT NULL,
    reason TEXT NOT NULL,
    risk TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    decision TEXT,
    reviewer_feedback TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    CONSTRAINT fk_human_reviews_run
        FOREIGN KEY (conversation_id, run_id)
        REFERENCES runs (conversation_id, run_id),
    CONSTRAINT fk_human_reviews_requested_session
        FOREIGN KEY (conversation_id, requested_session_id)
        REFERENCES sessions (conversation_id, session_id),
    CONSTRAINT fk_human_reviews_decision_session
        FOREIGN KEY (conversation_id, decision_session_id)
        REFERENCES sessions (conversation_id, session_id),
    CONSTRAINT uq_human_reviews_candidate
        UNIQUE (conversation_id, run_id, candidate_id),
    CONSTRAINT ck_human_reviews_risk
        CHECK (risk IN ('low', 'medium', 'high')),
    CONSTRAINT ck_human_reviews_status
        CHECK (status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT ck_human_reviews_decision
        CHECK (
            (status = 'pending' AND decision IS NULL AND decision_session_id IS NULL AND decided_at IS NULL)
            OR (
                status IN ('approved', 'rejected')
                AND decision IN ('approve', 'reject')
                AND decision_session_id IS NOT NULL
                AND decided_at IS NOT NULL
            )
        )
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL,
    session_id UUID NOT NULL,
    run_id UUID NOT NULL,
    file_name TEXT NOT NULL,
    local_path TEXT NOT NULL,
    artifact_type TEXT NOT NULL DEFAULT 'sql',
    status TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    known_differences JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_artifacts_session
        FOREIGN KEY (conversation_id, session_id)
        REFERENCES sessions (conversation_id, session_id),
    CONSTRAINT fk_artifacts_run
        FOREIGN KEY (conversation_id, run_id)
        REFERENCES runs (conversation_id, run_id),
    CONSTRAINT uq_artifact_path
        UNIQUE (local_path),
    CONSTRAINT uq_artifact_name_per_run
        UNIQUE (conversation_id, run_id, file_name),
    CONSTRAINT ck_artifacts_status
        CHECK (status IN ('validated', 'human_approved')),
    CONSTRAINT ck_artifacts_sha256
        CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS ix_conversations_updated
    ON conversations (updated_at DESC, conversation_id);
CREATE INDEX IF NOT EXISTS ix_sessions_conversation_started
    ON sessions (conversation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_runs_conversation_started
    ON runs (conversation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_messages_conversation_created
    ON messages (conversation_id, created_at, message_id);
CREATE INDEX IF NOT EXISTS ix_messages_run_created
    ON messages (run_id, created_at, message_id);
CREATE INDEX IF NOT EXISTS ix_messages_tool_call
    ON messages (tool_call_id)
    WHERE tool_call_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_agent_events_run_created
    ON agent_events (run_id, created_at, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_events_human_review
    ON agent_events ((metadata ->> 'review_id'))
    WHERE event_type = 'human_review_required';
CREATE INDEX IF NOT EXISTS ix_uploaded_sources_scope
    ON uploaded_sources (conversation_id, session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_human_reviews_pending
    ON human_reviews (conversation_id, requested_at)
    WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS uq_human_reviews_one_pending_per_run
    ON human_reviews (conversation_id, run_id)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS ix_artifacts_run_created
    ON artifacts (conversation_id, run_id, created_at);
