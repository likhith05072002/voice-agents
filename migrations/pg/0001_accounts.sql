-- 0001_accounts.sql — users, workspaces, sessions, and tenant-scoped
-- agents/calls. First Postgres schema (accounts mode). Legacy SQLite mode
-- (DATABASE_URL unset) never runs this.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    google_sub    text UNIQUE NOT NULL,
    email         text NOT NULL,
    name          text NOT NULL DEFAULT '',
    picture       text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_key ON users (lower(email));

CREATE TABLE IF NOT EXISTS workspaces (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL,
    owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS workspaces_owner_idx ON workspaces (owner_user_id);

-- Solo workspaces today, but membership is its own table so team invites and
-- roles drop in later without a schema change.
CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         text NOT NULL DEFAULT 'owner',
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS wm_user_idx ON workspace_members (user_id);

-- Server-side sessions: revocable (logout deletes the row). Only the sha256 of
-- the cookie token is stored, so a DB leak can't be replayed as a session.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash   text PRIMARY KEY,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions (expires_at);

-- Agents: same JSON-blob model as the SQLite store (new config fields never
-- need a migration) plus a real workspace_id column so tenant filtering is
-- indexed, not parsed. agent_id stays globally unique BY CONSTRUCTION (the
-- create endpoint suffixes collisions) so the in-memory AgentStore, call
-- registry, and WS routing keep their flat keys. NULL workspace_id = platform
-- agents (the public landing demo, the default fallback persona).
CREATE TABLE IF NOT EXISTS agents (
    agent_id     text PRIMARY KEY,
    workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
    json         jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agents_ws_idx ON agents (workspace_id);

-- Calls: column parity with the SQLite store + workspace_id for tenant reads.
-- call_id can be '' (loopback tests), so uniqueness is enforced only on real
-- carrier ids via a partial index; a surrogate identity PK keeps every row.
CREATE TABLE IF NOT EXISTS calls (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    call_id          text NOT NULL DEFAULT '',
    workspace_id     uuid,
    agent_id         text,
    from_number      text,
    to_number        text,
    started_at       double precision,
    ended_at         double precision,
    duration_s       double precision,
    turn_count       integer,
    avg_perceived_ms double precision,
    outcome          text,
    turns            text,
    metrics          text,
    metadata         text
);
CREATE UNIQUE INDEX IF NOT EXISTS calls_ccid_key ON calls (call_id) WHERE call_id <> '';
CREATE INDEX IF NOT EXISTS calls_ws_idx ON calls (workspace_id, started_at DESC);
CREATE INDEX IF NOT EXISTS calls_agent_idx ON calls (agent_id, started_at DESC);
