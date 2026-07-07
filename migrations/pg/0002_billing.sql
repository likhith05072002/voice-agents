-- 0002_billing.sql — prepaid credits (wallet + auditable ledger), API keys,
-- and payment orders. Money is INTEGER PAISE everywhere (no floats).

-- One wallet per user (spans all their workspaces). balance_paise is a cached
-- aggregate of the ledger — the ledger is the source of truth for audit.
CREATE TABLE IF NOT EXISTS wallets (
    user_id       uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    balance_paise bigint NOT NULL DEFAULT 0,
    trial_granted boolean NOT NULL DEFAULT false,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Every credit movement: trial grant, top-up, per-call usage, manual adjust.
-- delta_paise is signed (+grant/-usage). balance_after snapshots the wallet
-- right after the movement so statements reconcile without replaying.
CREATE TABLE IF NOT EXISTS credit_ledger (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta_paise   bigint NOT NULL,
    balance_after bigint NOT NULL,
    kind          text NOT NULL,             -- trial_grant | topup | call_usage | adjustment
    ref           text NOT NULL DEFAULT '',  -- call_id / payment id / note
    seconds       double precision,          -- call usage detail (NULL otherwise)
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ledger_user_idx ON credit_ledger (user_id, id DESC);

-- Programmatic access (ElevenLabs-style). Only the sha256 of the key is
-- stored; the raw `sk_sonus_...` is shown ONCE at creation.
CREATE TABLE IF NOT EXISTS api_keys (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         text NOT NULL DEFAULT '',
    key_prefix   text NOT NULL,              -- first chars, for display ("sk_sonus_ab12…")
    key_hash     text UNIQUE NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at   timestamptz
);
CREATE INDEX IF NOT EXISTS api_keys_user_idx ON api_keys (user_id);

-- Payment orders (Razorpay). Wallet is credited ONLY on verified capture, and
-- exactly once (status flip inside a transaction guards double-credit).
CREATE TABLE IF NOT EXISTS payments (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider      text NOT NULL DEFAULT 'razorpay',
    order_id      text UNIQUE NOT NULL,      -- provider order id (or dev:...)
    payment_id    text NOT NULL DEFAULT '',  -- provider payment id once paid
    amount_paise  bigint NOT NULL,
    status        text NOT NULL DEFAULT 'created',  -- created | paid | failed
    created_at    timestamptz NOT NULL DEFAULT now(),
    paid_at       timestamptz
);
CREATE INDEX IF NOT EXISTS payments_user_idx ON payments (user_id, created_at DESC);
