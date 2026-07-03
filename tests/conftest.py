"""Shared test setup: configure the app's env BEFORE src.main is imported.

Several tests import the FastAPI app, whose settings are read at import time.
Setting these here (conftest is imported before test modules) keeps those tests
hermetic — no real keys, no persistence side-effects, an isolated agents DB.
"""

import os
import tempfile

_agents_db = os.path.join(tempfile.gettempdir(), "voiceagent_test_agents.db")
for _ext in ("", "-wal", "-shm"):
    try:
        os.remove(_agents_db + _ext)
    except OSError:
        pass

os.environ.setdefault("SARVAM_API_KEY", "test")
os.environ.setdefault("ENABLE_PERSISTENCE", "false")
os.environ.setdefault("OUTBOUND_API_KEY", "secret")
os.environ.setdefault("ADMIN_API_KEY", "admin-secret")
os.environ.setdefault("TELNYX_CONNECTION_ID", "")
os.environ.setdefault("AGENTS_DB_PATH", _agents_db)
