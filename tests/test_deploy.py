"""Deploy readiness: bundled data, Blueprint settings, health check, pause switch, headers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from data_agent import paths
from data_agent.web.app import BUNDLED_DB, create_app
from data_agent.web.budget import DailyBudget
from data_agent.web.demo import DemoModel

RENDER_YAML = paths.REPO_ROOT / "render.yaml"


def test_bundled_database_is_the_verified_benchmark_copy() -> None:
    manifest = json.loads((paths.EVAL_DIR / "data_manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(BUNDLED_DB.read_bytes()).hexdigest()
    assert digest == manifest["databases_sha256"]["student_club"]
    assert (BUNDLED_DB.parent / "NOTICE.md").is_file()  # CC BY-SA attribution ships with it


def test_blueprint_settings() -> None:
    text = RENDER_YAML.read_text(encoding="utf-8")
    assert "data_agent.web.app:app" in text and "--proxy-headers" in text
    assert "key: FORWARDED_ALLOW_IPS" in text  # env var, not a shell-quoted "*" argument
    assert "healthCheckPath: /healthz" in text
    assert "autoDeployTrigger: checksPass" in text
    # The API key is declared but never given a value in git.
    key_block = text.split("key: OPENAI_API_KEY", 1)[1].split("- key:", 1)[0]
    assert "sync: false" in key_block and "value:" not in key_block
    db_line = next(line for line in text.splitlines() if "deploy/data/" in line)
    assert (paths.REPO_ROOT / db_line.split("value:", 1)[1].strip()).is_file()


def app(tmp_path: Path, paused: bool = False) -> TestClient:
    return TestClient(
        create_app(
            DemoModel, BUNDLED_DB, DailyBudget(1.0, tmp_path / "b.json"), paused=lambda: paused
        )
    )


def test_health_check_and_security_headers(tmp_path: Path) -> None:
    r = app(tmp_path).get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    csp = r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert app(tmp_path).get("/openapi.json").status_code == 404  # no API docs in production


def test_pause_switch_refuses_without_calling_the_model(tmp_path: Path) -> None:
    class ExplodingModel:
        model = "gpt-4.1-mini"

        def chat(self, *a: object, **k: object) -> object:
            raise AssertionError("the model must not be called while paused")

    client = TestClient(
        create_app(ExplodingModel, BUNDLED_DB, DailyBudget(1.0), paused=lambda: True)  # type: ignore[arg-type]
    )
    d = client.post("/api/ask", json={"question": "How many events are there?"}).json()
    assert d["status"] == "paused"
    assert client.get("/api/config").json()["paused"] is True
