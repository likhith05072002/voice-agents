-- 0003_numbers.sql — the platform phone-number pool.
--
-- Pool model (see BUSINESS-PHONE-NUMBERS.md): the operator stocks numbers
-- (bought on Telnyx / an Indian cloud-telephony partner), customers claim one
-- for an agent, monthly rent is charged from the same credits wallet
-- (ledger kind 'number_rent'). Inbound routing already resolves by DID via
-- AgentConfig.phone_numbers — claiming just attaches the DID to the agent.

CREATE TABLE IF NOT EXISTS phone_numbers (
    number        text PRIMARY KEY,           -- E.164, e.g. +15572046319
    country       text NOT NULL DEFAULT 'US', -- ISO country of the DID
    monthly_paise bigint NOT NULL DEFAULT 19900,
    status        text NOT NULL DEFAULT 'available',  -- available | assigned
    workspace_id  uuid REFERENCES workspaces(id) ON DELETE SET NULL,
    agent_id      text,                       -- agent it rings (when assigned)
    assigned_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    notes         text NOT NULL DEFAULT ''    -- operator notes (carrier, order id)
);
CREATE INDEX IF NOT EXISTS numbers_ws_idx ON phone_numbers (workspace_id);
CREATE INDEX IF NOT EXISTS numbers_status_idx ON phone_numbers (status, country);
