"""Actual backend, browser settings, hot reload and restart with a synthetic IPC peer."""

from __future__ import annotations

import mimetypes
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings

from .test_jev_runtime_e2e import chromium_page as chromium_page
from .test_sponsored_user_flow import COMMAND

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


@pytest.fixture
def sponsored_app(
    tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[tuple[str, Path, Any]]:
    cfg = Config()
    cfg.data_dir = str(tmp_path / "data")
    cfg.llm.instance_routing = True
    cfg.llm.instances = {}
    cfg.llm.default_chain = []
    cfg.llm.embedding.provider = ""
    cfg.llm.sponsored = SponsoredRuntimeSettings(
        enabled=getattr(request, "param", "legacy") != "fresh",
        runtime_path=shlex.join(COMMAND),
    )
    cfg.scheduler.auto_update_enabled = False
    cfg.network.mode = "direct"
    for field in fields(cfg.sources):
        source = getattr(cfg.sources, field.name)
        if hasattr(source, "enabled"):
            source.enabled = field.name == "xiaohongshu"
    save_config(cfg, tmp_path / "config.toml")
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "TMPDIR"}}
    env.update(PYTHONPATH=str(ROOT / "src"), OPENBILICLAW_PROJECT_ROOT=str(tmp_path))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = None
    with (tmp_path / "server.log").open("w+") as log:

        def restart(*, error_code: str = "") -> None:
            nonlocal process
            env["SPONSORED_MOCK_ERROR_CODE"] = error_code
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "openbiliclaw.api.app:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            (tmp_path / "server.pid").write_text(str(process.pid))
            with httpx.Client(base_url=url, trust_env=False, timeout=2) as client:
                # Cold Python imports on a busy developer machine can exceed
                # 40s before Uvicorn starts. This only bounds fixture readiness;
                # browser actions and provider requests keep their own budgets.
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    assert process.poll() is None, (tmp_path / "server.log").read_text()[-4000:]
                    try:
                        if client.get("/api/config").status_code == 200:
                            return
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                pytest.fail(
                    "backend startup timed out\n" + (tmp_path / "server.log").read_text()[-4000:]
                )

        try:
            restart()
            yield url, tmp_path, restart
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def wait_applied(url: str) -> None:
    with httpx.Client(base_url=url, trust_env=False, timeout=5) as client:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = client.get("/api/config/apply-status").json()
            if result.get("state") in {"idle", "applied"}:
                return
            assert result.get("state") != "failed", result
            time.sleep(0.1)
        pytest.fail(f"config did not apply: {result}")


def probe_primary(client: Any) -> dict[str, Any]:
    llm = client.get("/api/config").json()["llm"]
    return client.post(
        "/api/config/probe-service",
        json={
            "kind": "llm_instance",
            "instance_id": llm["default_chain"][0],
        },
    ).json()


def select_model_instance(page: Any, model: str, *, popup: bool = False) -> None:
    # Choose a fixed entry through the ordinary default chain, never an editor.
    card_attr = "data-llm-instance-id" if popup else "data-instance-card"
    card = page.locator(f"[{card_attr}]").filter(has_text=model).first
    card.wait_for()
    instance_id = card.get_attribute(card_attr)
    action_attr = "data-llm-instance-action" if popup else "data-instance-action"
    assert card.locator(f'[{action_attr}="edit"]').count() == 0
    assert card.locator(f'[{action_attr}="delete"]').count() == 0
    prefix = "cfgLlm" if popup else "llm"
    chain = page.locator(f"#{prefix}DefaultChain")
    item = chain.locator(f'[data-instance-id="{instance_id}"]')
    if not item.count():
        page.locator(f"#{prefix}DefaultChainPicker").select_option(instance_id)
        page.locator("#cfgAddLlmDefaultChainItem" if popup else "#addLlmDefaultChainItem").click()
    action = "data-llm-chain-action" if popup else "data-chain-action"
    up = item.locator(f'[{action}="up"]')
    while up.is_enabled():
        up.click()


@pytest.mark.parametrize("sponsored_app", ["fresh"], indirect=True)
@pytest.mark.parametrize("initial_model", ["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"])
def test_setup_without_a_key_then_desktop_switch_and_restart(
    sponsored_app: tuple,
    chromium_page: Any,
    initial_model: str,
) -> None:
    url, directory, restart = sponsored_app
    page = chromium_page
    # An installed catalogue is not a selected chain. Start with neither
    # capability active so onboarding, rather than legacy migration, must
    # actually select and persist the user's chosen instance.
    before = load_config(directory / "config.toml", consult_environment=False)
    assert before.llm.default_chain == []
    assert before.llm.embedding.provider == ""
    assert not before.llm.sponsored.enabled
    page.goto(url + "/setup/")
    page.locator("#modelFreeChoice").wait_for()
    assert page.locator("#provider").input_value() == "siliconflow_free"
    page.locator("#modelFreeChoice").select_option(initial_model)
    assert not page.locator("#apiKey").is_visible()
    assert not page.locator("#reasoningEffort").is_visible()
    assert not page.locator('label[for="reasoningEffort"]').is_visible()
    artifacts = ROOT / "output/provider-unification"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / "fixed-01-setup.png"))
    assert page.locator("#setupEmbeddingProvider").input_value() == "siliconflow_free"
    with page.expect_response(
        lambda r: r.url.endswith("/api/config") and r.request.method == "PUT"
    ) as saving:
        page.locator("#saveLlm").click()
    assert saving.value.status in {200, 202}, saving.value.text()
    page.locator("#doneLlm").filter(has_text="已连接").wait_for(state="attached")
    wait_applied(url)
    configured = load_config(directory / "config.toml", consult_environment=False)
    assert len(configured.llm.default_chain) == 1
    primary = configured.llm.default_chain[0]
    assert configured.llm.instances[primary].model == initial_model
    assert configured.llm.embedding.provider == "siliconflow_free"
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
        result = client.post("/api/config/probe-service", json={"kind": "llm_chain"}).json()
        assert result["ok"] and result["model"] == initial_model, result
        assert result["instance_id"] == primary
        assert client.post("/api/config/probe-service", json={"kind": "embedding"}).json()["ok"]
    switched_model = "Qwen/Qwen3.5-4B" if initial_model == "Qwen/Qwen3-8B" else "Qwen/Qwen3-8B"
    page.goto(url + "/web/?settings=models")
    assert page.locator("#sponsoredSettings").count() == 0
    select_model_instance(page, switched_model)
    assert page.locator("#embeddingModel").evaluate("el => el.readOnly")
    assert page.locator("#embeddingOutputDimensionality").evaluate("el => el.readOnly")
    assert not page.locator("#embeddingApiKey").is_visible()
    page.locator("#llmInstanceList").evaluate("el => el.scrollIntoView({block: 'center'})")
    page.locator("#llmInstanceList").screenshot(path=str(artifacts / "fixed-02-pc-instances.png"))
    with page.expect_response(
        lambda r: r.url.endswith("/api/config") and r.request.method == "PUT"
    ) as saving:
        page.get_by_role("button", name="保存配置", exact=True).click()
    assert saving.value.status == 202, saving.value.text()
    wait_applied(url)
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
        result = probe_primary(client)
        assert result["ok"] and result["model"] == switched_model, result
        assert client.post("/api/config/probe-service", json={"kind": "embedding"}).json()["ok"]
    config = load_config(directory / "config.toml", consult_environment=False)
    assert config.llm.instances and config.llm.embedding.provider == "siliconflow_free"
    assert len(config.llm.default_chain) == 2
    assert config.llm.default_chain[1] == primary
    assert not config.llm.sponsored.enabled
    restart()
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
        result = probe_primary(client)
        assert result["ok"] and result["model"] == switched_model, result


def test_popup_and_mobile_share_saved_model_with_real_backend(
    sponsored_app: tuple, chromium_page: Any
) -> None:
    url, _directory, _restart = sponsored_app
    page = chromium_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": 420, "height": 900})

    def assets(route: Any) -> None:
        name = urlsplit(route.request.url).path.removeprefix("/popup/")
        file = (
            ROOT
            / ("src/openbiliclaw/web" if name.startswith("shared/") else "extension/popup")
            / name
        )
        if not file.is_file():
            route.fulfill(status=404, body="not found")
            return
        route.fulfill(
            content_type=mimetypes.guess_type(file.name)[0] or "text/plain", body=file.read_bytes()
        )

    page.route("**/popup/**", assets)
    port = urlsplit(url).port
    page.add_init_script(f"""
    const store={{popup_backend_endpoint:{{scheme:'http',host:'127.0.0.1',port:{port}}}}};
    window.chrome={{storage:{{local:{{
      get:(keys,cb)=>{{cb?.(store);return Promise.resolve(store);}},
      set:(values,cb)=>{{Object.assign(store,values);cb?.();return Promise.resolve();}},
      remove:(keys,cb)=>{{cb?.();return Promise.resolve();}}
    }},onChanged:{{addListener(){{}}}}}},runtime:{{
      id:'sponsored-fixture',getURL:path=>'/popup/'+path,
      getManifest:()=>({{version:'0.0.0'}}),sendMessage:async()=>({{}}),
      onMessage:{{addListener(){{}},removeListener(){{}}}}
    }},tabs:{{create:async()=>({{}})}}}};
    """)
    page.goto(url + "/popup/popup.html")
    page.locator("#settingsGear").click()
    select_model_instance(page, "Qwen/Qwen3.5-4B", popup=True)
    artifacts = ROOT / "output/provider-unification"
    artifacts.mkdir(parents=True, exist_ok=True)
    assert page.locator("#cfgEmbeddingModel").evaluate("el => el.readOnly")
    page.locator("#cfgLlmInstanceList").evaluate("el => el.scrollIntoView({block: 'center'})")
    page.locator("#cfgLlmInstanceList").screenshot(path=str(artifacts / "fixed-03-extension.png"))
    with page.expect_response(
        lambda r: r.url.endswith("/api/config") and r.request.method == "PUT"
    ) as saving:
        page.locator("#settingsSave").click()
    assert saving.value.status == 202, saving.value.text()
    wait_applied(url)
    page.goto(url + "/m/#/recommend")
    page.get_by_role("button", name="打开内容来源、保存与候选评估设置").click()
    page.locator(".mobile-settings-status").filter(has_text="设置已加载").wait_for()
    assert page.locator("#mobile-llm-instance option:checked").inner_text().find("Qwen3.5-4B") >= 0
    assert page.locator("#mobile-llm-model").count() == 0
    page.set_viewport_size({"width": 320, "height": 900})
    for selector in ("#mobile-llm-instance", "#mobile-embedding"):
        box = page.locator(selector).bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 320
        assert box["height"] >= 44
    page.set_viewport_size({"width": 420, "height": 900})
    page.screenshot(path=str(artifacts / "fixed-04-mobile.png"))
    option = page.locator("#mobile-llm-instance option").filter(has_text="Qwen3-8B")
    page.locator("#mobile-llm-instance").select_option(option.get_attribute("value"))
    page.get_by_role("button", name="保存设置", exact=True).click()
    page.locator(".mobile-settings-status").filter(has_text="设置已保存").wait_for()
    wait_applied(url)
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
        result = probe_primary(client)
        assert result["ok"] and result["model"] == "Qwen/Qwen3-8B", result

    _restart(error_code="AUTH_FAILED")
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
        assert not probe_primary(client)["ok"]
    page.goto(url + "/popup/popup.html")
    page.locator("#settingsGear").click()
    notice = page.locator("#cfgLlmProviderNotice")
    notice.get_by_text("免费方案凭据不可用", exact=True).wait_for()
    assert page.locator("#cfgEmbeddingProvider").input_value() == "siliconflow_free"
    artifacts = ROOT / "output/playwright"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.locator("#cfgLlmProviderNotice").screenshot(
        path=str(artifacts / "sponsored-renewal-popup.png")
    )
    page.goto(url + "/popup/popup.html")
    page.locator("#messagesButton").click()
    page.locator("#sponsoredMessageNotice").get_by_text("免费方案凭据不可用", exact=True).wait_for()
    assert page.locator("#messageBadge").inner_text() != "0"
    page.goto(url + "/m/#/chat")
    page.get_by_role("button", name="查看消息", exact=True).click()
    notice = page.locator(".messages-overlay .sponsored-notice")
    notice.get_by_text("免费方案凭据不可用", exact=True).wait_for()
    page.evaluate("""async () => {
      const { patchState } = await import('/m/js/state.js');
      patchState({runtimeStatus: await (await fetch('/api/runtime-status')).json()});
    }""")
    notice.get_by_role("button", name="配置自己的 Key", exact=True).focus()
    page.evaluate("""async () => {
      const { state, patchState } = await import('/m/js/state.js');
      patchState({runtimeStatus: {...state.runtimeStatus, recommendation_count: 7}});
    }""")
    assert notice.get_by_role("button", name="配置自己的 Key", exact=True).evaluate(
        "button => document.activeElement === button"
    )
    notice.screenshot(path=str(artifacts / "sponsored-renewal-mobile.png"))
    notice.get_by_role("button", name="配置自己的 Key", exact=True).click()
    page.wait_for_url("**/web/?settings=models")
    page.locator("#llmProviderNotice").wait_for()
    assert not errors, errors


def test_revoked_key_notices_survive_restart_and_offer_recovery(
    sponsored_app: tuple,
    chromium_page: Any,
) -> None:
    url, _directory, restart = sponsored_app
    restart(error_code="AUTH_FAILED")
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
        assert not probe_primary(client)["ok"]
        assert client.get("/api/runtime-status").json()["sponsored_status"]["notices"][0][
            "requires_update"
        ]
    restart(error_code="AUTH_FAILED")
    page = chromium_page
    page.goto(url + "/web/?settings=models")
    notice = page.locator("#llmProviderNotice")
    notice.get_by_text("免费方案凭据不可用", exact=True).wait_for()
    assert notice.get_by_role("link", name="查看最新版本").get_attribute("href") == (
        "https://github.com/whiteguo233/OpenBiliClaw/releases/latest"
    )
    notice.get_by_role("button", name="配置自己的 Key").click()
    page.locator("#llmInstanceDialog").wait_for()
    # Opening an ordinary instance editor never changes or invokes the active route.
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
        assert client.get("/api/config").json()["llm"]["default_chain"][0] == "siliconflow-free"
    page.goto(url + "/web/")
    page.locator("#messagesBtn").click()
    page.locator("#sponsoredMessageNotice").get_by_text("免费方案凭据不可用", exact=True).wait_for()
    restart()
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
        assert probe_primary(client)["ok"]
        notices = client.get("/api/config").json()["llm"]["sponsored"]["service_status"]["notices"]
        assert all("chat" not in notice["capabilities"] for notice in notices)
        assert client.post("/api/config/probe-service", json={"kind": "embedding"}).json()["ok"]
        assert not client.get("/api/config").json()["llm"]["sponsored"]["service_status"]["notices"]


def test_setup_selects_local_vectors_before_the_local_model_is_ready(
    sponsored_app: tuple, chromium_page: Any
) -> None:
    url, directory, restart = sponsored_app
    cfg = load_config(directory / "config.toml", consult_environment=False)
    cfg.llm.embedding.provider = "ollama"
    cfg.llm.embedding.model = "existing-local-embedding"
    cfg.llm.embedding.base_url = "http://127.0.0.1:1/v1"
    save_config(cfg, directory / "config.toml")
    restart()
    page = chromium_page
    page.goto(url + "/setup/")
    choice = page.locator("#setupEmbeddingProvider")
    choice.wait_for()
    assert choice.input_value() == "ollama"
    choice.select_option("ollama")
    with page.expect_response(
        lambda r: r.url.endswith("/api/config") and r.request.method == "PUT"
    ) as saving:
        page.locator("#saveLlm").click()
    assert saving.value.status == 202, saving.value.text()
    page.locator("#doneLlm").filter(has_text="已连接").wait_for(state="attached")
    wait_applied(url)
    saved = load_config(directory / "config.toml", consult_environment=False)
    assert saved.llm.embedding.provider == "ollama"
    assert saved.llm.embedding.model == "existing-local-embedding"
    assert saved.llm.embedding.base_url == "http://127.0.0.1:1/v1"
    restart()
    page.goto(url + "/web/?settings=models")
    page.wait_for_function("document.querySelector('#embeddingProvider')?.value === 'ollama'")
    assert page.locator("#embeddingProvider").input_value() == "ollama"
