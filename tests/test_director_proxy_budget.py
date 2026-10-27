"""The main API proxy covers legal foreground planning plus decision budgets."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.config import Config
from openbiliclaw.recommendation_runtime import RECOMMENDATION_PORT_ENV, RECOMMENDATION_SOCK_ENV
from tests.test_recommendation_proxy_headers import (
    _build_proxied_app,
    _FakeAsyncClient,
)


@pytest.mark.parametrize("transport", ["tcp", "uds"])
def test_proxy_deadline_covers_both_director_budgets(tmp_path, monkeypatch, transport):
    config = Config(data_dir=str(tmp_path))
    config.director.mode = "enforce"
    config.director.planning_budget_seconds = 60.0
    config.director.decision_budget_seconds = 60.0
    # This test owns the proxy only; the actual store/runtime is integration
    # tested separately. Its inherited fake DB cannot construct a runtime.
    monkeypatch.setattr(RuntimeContext, "configure_director", lambda *args: None)
    if transport == "tcp":
        monkeypatch.setenv(RECOMMENDATION_PORT_ENV, "18424")
    else:
        monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
        monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, "/tmp/director-test.sock")
    app = _build_proxied_app(monkeypatch, tmp_path, config=config)
    captured = []

    class CaptureClient(_FakeAsyncClient):
        def __init__(self, **kwargs):
            captured.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", CaptureClient)
    response = TestClient(app).post(
        "/api/recommendations/director/generate",
        json={"request_id": "one"},
    )
    assert response.status_code == 200
    assert captured[-1]["timeout"] == 150.0
    assert captured[-1]["trust_env"] is False
