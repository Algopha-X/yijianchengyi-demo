-- Candidate v1 data contract. Freeze only after the contract MR receives group approval.
-- M04 installs this schema through its migration; every connection enables foreign_keys.
PRAGMA foreign_keys = ON;

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash) = 64),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    owner_session_id TEXT NOT NULL REFERENCES sessions(id),
    kind TEXT NOT NULL CHECK(kind IN ('upload', 'preview')),
    path TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL CHECK(mime_type IN ('image/jpeg', 'image/png')),
    bytes INTEGER NOT NULL CHECK(bytes > 0),
    width INTEGER NOT NULL CHECK(width > 0),
    height INTEGER NOT NULL CHECK(height > 0),
    created_at TEXT NOT NULL,
    UNIQUE(id, owner_session_id)
);
CREATE INDEX assets_owner_created ON assets(owner_session_id, created_at);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    owner_session_id TEXT NOT NULL REFERENCES sessions(id),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    scenario TEXT NOT NULL CHECK(scenario IN ('poster', 'article_image')),
    style TEXT CHECK(style IN ('fresh', 'asymmetric', 'lively', 'elegant', 'modern', 'retro')),
    raw_text TEXT NOT NULL,
    fields_json TEXT NOT NULL CHECK(json_valid(fields_json)),
    user_edited_fields_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(user_edited_fields_json)),
    asset_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(asset_ids_json) AND json_array_length(asset_ids_json) <= 1),
    template_id TEXT,
    template_version INTEGER CHECK(template_version >= 1),
    analysis_json TEXT CHECK(analysis_json IS NULL OR json_valid(analysis_json)),
    analysis_mode TEXT CHECK(analysis_mode IN ('mock', 'real')),
    confirmed_revision INTEGER CHECK(confirmed_revision = revision),
    preview_asset_id TEXT,
    preview_revision INTEGER CHECK(preview_revision >= 1 AND preview_revision <= revision),
    phase TEXT NOT NULL DEFAULT 'draft' CHECK(phase IN ('draft', 'needs_input', 'needs_confirmation', 'ready_to_render', 'rendering', 'preview_ready', 'failed')),
    last_error_json TEXT CHECK(last_error_json IS NULL OR json_valid(last_error_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(id, owner_session_id),
    FOREIGN KEY(preview_asset_id, owner_session_id) REFERENCES assets(id, owner_session_id)
);
CREATE INDEX tasks_owner_updated ON tasks(owner_session_id, updated_at);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    owner_session_id TEXT NOT NULL REFERENCES sessions(id),
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('analyze', 'render')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'superseded')),
    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
    stage TEXT NOT NULL DEFAULT 'queued' CHECK(stage IN ('queued', 'classifying', 'recommending', 'checking_fields', 'polishing', 'validating', 'adapting_images', 'rendering', 'done', 'failed', 'superseded')),
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(owner_session_id, idempotency_key),
    FOREIGN KEY(task_id, owner_session_id) REFERENCES tasks(id, owner_session_id)
);
CREATE INDEX jobs_owner_created ON jobs(owner_session_id, created_at);
CREATE INDEX jobs_queue ON jobs(status, created_at);
CREATE UNIQUE INDEX jobs_one_active_per_task ON jobs(task_id) WHERE status IN ('queued', 'running');
