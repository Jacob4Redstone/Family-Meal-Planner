import random

import pytest
from helpers import appmod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(appmod.app.config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(appmod, "AUTH_PASSWORD", "")
    monkeypatch.setattr(appmod, "ENABLE_URL_IMPORT", False)
    appmod.init_db()
    random.seed(1234)
    c = appmod.app.test_client()
    c.environ_base["HTTP_X_REQUESTED_WITH"] = "fetch"
    return c
