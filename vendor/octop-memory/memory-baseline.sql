-- openGauss memory baseline; derived from octop-memory 1.0.2 (MIT).
CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

CREATE TABLE IF NOT EXISTS memory_nodes (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    parent_id TEXT,
                    level TEXT NOT NULL CHECK (level IN ('root', 'branch', 'leaf')),
                    atom_id TEXT,
                    content TEXT NOT NULL,
                    topic TEXT,
                    conversation_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    content_tsv TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('simple', content) || to_tsvector('simple', COALESCE(topic, ''))
                    ) STORED,
                    PRIMARY KEY (namespace, id),
                    UNIQUE (namespace, atom_id),
                    FOREIGN KEY (namespace, parent_id)
                        REFERENCES memory_nodes (namespace, id)
                        DEFERRABLE INITIALLY IMMEDIATE
                );

CREATE INDEX IF NOT EXISTS memory_nodes_parent ON memory_nodes(namespace, parent_id);

CREATE INDEX IF NOT EXISTS memory_nodes_level ON memory_nodes(namespace, level);

CREATE INDEX IF NOT EXISTS memory_nodes_fts ON memory_nodes USING GIN(content_tsv);

CREATE TABLE IF NOT EXISTS raw_events (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    host TEXT NOT NULL,
                    session_id TEXT,
                    thread_id TEXT,
                    "user" TEXT,
                    timestamp TIMESTAMPTZ NOT NULL,
                    event_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
                    PRIMARY KEY (namespace, id)
                );

CREATE INDEX IF NOT EXISTS raw_events_time ON raw_events(namespace, timestamp);

CREATE INDEX IF NOT EXISTS raw_events_session ON raw_events(namespace, session_id);

CREATE INDEX IF NOT EXISTS raw_events_thread ON raw_events(namespace, thread_id);

CREATE INDEX IF NOT EXISTS raw_events_host ON raw_events(namespace, host);

CREATE INDEX IF NOT EXISTS raw_events_type ON raw_events(namespace, event_type);

CREATE INDEX IF NOT EXISTS raw_events_fts ON raw_events USING GIN(content_tsv);

CREATE TABLE IF NOT EXISTS candidates (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    raw_event_ids JSONB NOT NULL,
                    candidate_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    assertion TEXT NOT NULL,
                    verbatim_quote TEXT NOT NULL,
                    quote_event_id TEXT NOT NULL,
                    subject_name TEXT NOT NULL,
                    subject_entity_type TEXT NOT NULL,
                    target_entity_id TEXT,
                    confidence TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    recommended_action TEXT NOT NULL,
                    promotion_reason TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    decided_at TIMESTAMPTZ,
                    decided_by TEXT,
                    session_id TEXT,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    fts_tsv TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('simple', title || ' ' || assertion || ' ' || verbatim_quote)
                    ) STORED,
                    PRIMARY KEY (namespace, id)
                );

CREATE INDEX IF NOT EXISTS candidates_status ON candidates(namespace, status);

CREATE INDEX IF NOT EXISTS candidates_session ON candidates(namespace, session_id);

CREATE INDEX IF NOT EXISTS candidates_target_entity ON candidates(namespace, target_entity_id);

CREATE INDEX IF NOT EXISTS candidates_created_at ON candidates(namespace, created_at);

CREATE INDEX IF NOT EXISTS candidates_fts ON candidates USING GIN(fts_tsv);

CREATE TABLE IF NOT EXISTS atoms (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    raw_event_ids JSONB NOT NULL,
                    assertion TEXT NOT NULL,
                    verbatim_quote TEXT NOT NULL,
                    quote_event_id TEXT NOT NULL,
                    search_terms JSONB NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    confidence TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    superseded_by TEXT,
                    deprecated_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL,
                    fts_tsv TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector(
                            'simple',
                            assertion || ' ' || verbatim_quote || ' ' ||
                            COALESCE(search_terms::text, '')
                        )
                    ) STORED,
                    PRIMARY KEY (namespace, id)
                );

CREATE INDEX IF NOT EXISTS atoms_entity ON atoms(namespace, entity_id);

CREATE INDEX IF NOT EXISTS atoms_importance ON atoms(namespace, importance);

CREATE INDEX IF NOT EXISTS atoms_created_at ON atoms(namespace, created_at);

CREATE INDEX IF NOT EXISTS atoms_occurred_at ON atoms(namespace, occurred_at);

CREATE INDEX IF NOT EXISTS atoms_superseded ON atoms(namespace, superseded_by);

CREATE INDEX IF NOT EXISTS atoms_fts ON atoms USING GIN(fts_tsv);

CREATE TABLE IF NOT EXISTS entities (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
                    atom_count INTEGER NOT NULL DEFAULT 0,
                    last_promoted_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (namespace, id)
                );

CREATE INDEX IF NOT EXISTS entities_type ON entities(namespace, entity_type);

CREATE INDEX IF NOT EXISTS entities_name ON entities(namespace, LOWER(canonical_name));

CREATE TABLE IF NOT EXISTS aliases (
                    namespace TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (namespace, alias, entity_id)
                );

CREATE INDEX IF NOT EXISTS aliases_alias ON aliases(namespace, alias);

CREATE INDEX IF NOT EXISTS aliases_entity ON aliases(namespace, entity_id);

CREATE TABLE IF NOT EXISTS entity_pages (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    summary_markdown TEXT NOT NULL DEFAULT '',
                    headline TEXT NOT NULL DEFAULT '',
                    topics JSONB NOT NULL DEFAULT '[]'::jsonb,
                    dirty BOOLEAN NOT NULL DEFAULT TRUE,
                    regen_attempt_count INTEGER NOT NULL DEFAULT 0,
                    summary_version INTEGER NOT NULL DEFAULT 0,
                    last_regen_at TIMESTAMPTZ,
                    last_user_edit_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (namespace, id),
                    UNIQUE (namespace, entity_id)
                );

CREATE INDEX IF NOT EXISTS entity_pages_dirty ON entity_pages(namespace, dirty);

CREATE INDEX IF NOT EXISTS entity_pages_updated ON entity_pages(namespace, updated_at);

CREATE INDEX IF NOT EXISTS entity_pages_last_regen ON entity_pages(namespace, last_regen_at);

CREATE TABLE IF NOT EXISTS thread_active_entities (
                    namespace TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    source TEXT NOT NULL DEFAULT 'recall_hit',
                    PRIMARY KEY (namespace, thread_id, entity_id)
                );

CREATE INDEX IF NOT EXISTS thread_active_entities_seen ON thread_active_entities(namespace, thread_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS journal (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    target_entity_id TEXT,
                    target_atom_id TEXT,
                    target_candidate_id TEXT,
                    before JSONB,
                    after JSONB,
                    note TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (namespace, id)
                );

CREATE INDEX IF NOT EXISTS journal_time ON journal(namespace, timestamp);

CREATE INDEX IF NOT EXISTS journal_action ON journal(namespace, action);

CREATE INDEX IF NOT EXISTS journal_target_entity ON journal(namespace, target_entity_id);

CREATE INDEX IF NOT EXISTS journal_target_atom ON journal(namespace, target_atom_id);

CREATE INDEX IF NOT EXISTS journal_target_candidate ON journal(namespace, target_candidate_id);

CREATE TABLE IF NOT EXISTS episodes (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    raw_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    summary TEXT NOT NULL,
                    verbatim_quote TEXT NOT NULL,
                    quote_event_id TEXT NOT NULL,
                    emotion TEXT NOT NULL DEFAULT 'neutral',
                    intensity INTEGER NOT NULL DEFAULT 1,
                    people JSONB NOT NULL DEFAULT '[]'::jsonb,
                    topics JSONB NOT NULL DEFAULT '[]'::jsonb,
                    extractor_version TEXT NOT NULL,
                    session_id TEXT,
                    digest_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL,
                    fts_tsv TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector(
                            'simple',
                            summary || ' ' || verbatim_quote || ' ' ||
                            COALESCE(people::text, '') || ' ' ||
                            COALESCE(topics::text, '')
                        )
                    ) STORED,
                    PRIMARY KEY (namespace, id)
                );

CREATE INDEX IF NOT EXISTS episodes_occurred ON episodes(namespace, occurred_at);

CREATE INDEX IF NOT EXISTS episodes_session ON episodes(namespace, session_id);

CREATE INDEX IF NOT EXISTS episodes_emotion ON episodes(namespace, emotion);

CREATE INDEX IF NOT EXISTS episodes_fts ON episodes USING GIN(fts_tsv);

CREATE TABLE IF NOT EXISTS digests (
                    namespace TEXT NOT NULL,
                    id TEXT NOT NULL,
                    period_kind TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    period_start TIMESTAMPTZ NOT NULL,
                    period_end TIMESTAMPTZ NOT NULL,
                    markdown TEXT NOT NULL,
                    episode_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    llm_version TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (namespace, id),
                    UNIQUE (namespace, period_kind, period_key)
                );

CREATE INDEX IF NOT EXISTS digests_start ON digests(namespace, period_start);
