"""Cross-layer drift locks for the complete GitHub source surface.

The GitHub adapter is intentionally backend-only: official REST requests are
made by the backend, while setup/settings/recommendation surfaces expose the
same source contract without granting the browser extension GitHub access.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import fields
from pathlib import Path
from typing import Any

from openbiliclaw.api.models import (
    GitHubSourceConfigOut,
    SourcesConfigOut,
    SourcesCredentialsResponse,
    SourcesStatusResponse,
)
from openbiliclaw.api.source_auth.providers import SOURCE_AUTH_PROVIDERS
from openbiliclaw.api.source_auth.verify import VERIFY_ACTIONS
from openbiliclaw.api.source_auth.write import CREDENTIAL_SPECS
from openbiliclaw.config import (
    GITHUB_ALLOWED_SOURCE_MODES,
    GITHUB_CONFIG_INTEGER_LIMITS,
    GITHUB_TOKEN_ENV,
    Config,
    SourcesConfig,
)
from openbiliclaw.runtime.source_policy import (
    DEFAULT_POOL_SOURCE_SHARES,
    DEFAULT_SOURCE_ENABLED,
    SOURCE_ORDER,
)
from openbiliclaw.sources.platforms import CANONICAL_SOURCE_FAMILIES

ROOT = Path(__file__).resolve().parents[1]


def _literal_assignment(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing module assignment: {name}")


def _js_string_array(source: str, name: str) -> tuple[str, ...]:
    match = re.search(
        rf"const\s+{re.escape(name)}\s*=\s*Object\.freeze\(\[(.*?)\]\);",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, f"missing JavaScript array: {name}"
    return tuple(re.findall(r'"([^"\\]+)"', match.group(1)))


def _assert_same_roster(name: str, actual: set[str], canonical: set[str]) -> None:
    assert actual == canonical, (
        f"{name} drifted: missing={sorted(canonical - actual)}, extra={sorted(actual - canonical)}"
    )


def test_canonical_source_roster_matches_every_central_registration_set() -> None:
    canonical_tuple = tuple(CANONICAL_SOURCE_FAMILIES)
    canonical = set(canonical_tuple)
    config_fields = {field.name for field in fields(SourcesConfig)} - {
        "browser_cdp_url",
        "browser_headed",
    }
    api_config_fields = set(SourcesConfigOut.model_fields) - {"browser"}
    app_path = ROOT / "src/openbiliclaw/api/app.py"
    shared_source = (ROOT / "src/openbiliclaw/web/shared/source-status.js").read_text(
        encoding="utf-8"
    )

    rosters = {
        "source-auth providers": set(SOURCE_AUTH_PROVIDERS),
        "verify actions": set(VERIFY_ACTIONS),
        "credential specs": set(CREDENTIAL_SPECS),
        "config source fields": config_fields,
        "status response fields": set(SourcesStatusResponse.model_fields),
        "credential response fields": set(SourcesCredentialsResponse.model_fields),
        "config response fields": api_config_fields,
        "source policy order": set(SOURCE_ORDER),
        "source policy enablement": set(DEFAULT_SOURCE_ENABLED),
        "source policy shares": set(DEFAULT_POOL_SOURCE_SHARES),
        "shared frontend source keys": set(_js_string_array(shared_source, "SOURCE_KEYS")),
        "API source-share order": set(_literal_assignment(app_path, "_SOURCE_SHARE_ORDER")),
        "API init order": set(_literal_assignment(app_path, "_INIT_SOURCE_ORDER")),
    }
    for name, roster in rosters.items():
        _assert_same_roster(name, roster, canonical)

    assert tuple(SOURCE_ORDER) == canonical_tuple
    assert tuple(_literal_assignment(app_path, "_SOURCE_SHARE_ORDER")) == canonical_tuple
    assert tuple(_literal_assignment(app_path, "_INIT_SOURCE_ORDER")) == canonical_tuple
    assert "github: Object.freeze({ guidedInit: true })" in shared_source
    assert 'github: "GitHub"' in shared_source


def test_github_config_is_write_only_with_three_modes_budgets_date_and_share() -> None:
    github = Config().sources.github
    api_model = GitHubSourceConfigOut()
    api_fields = set(GitHubSourceConfigOut.model_fields)
    spec = CREDENTIAL_SPECS["github"]

    assert github.enabled is False
    assert github.access_token == ""
    assert github.token_env == GITHUB_TOKEN_ENV == "OPENBILICLAW_GITHUB_TOKEN"
    assert github.source_modes == ("search", "ranked", "latest")
    assert {"search", "ranked", "latest"} == GITHUB_ALLOWED_SOURCE_MODES
    assert {
        "daily_search_budget",
        "daily_ranked_budget",
        "daily_latest_budget",
    } <= set(GITHUB_CONFIG_INTEGER_LIMITS)
    assert (
        github.daily_search_budget,
        github.daily_ranked_budget,
        github.daily_latest_budget,
    ) == (120, 60, 60)
    assert github.recommendation_date_preset == "all"
    assert github.recommendation_date_weight == 0.5
    assert Config().scheduler.pool_source_shares["github"] == 1

    assert "access_token" not in api_fields
    assert "access_token" not in api_model.model_dump()
    assert api_model.access_token_set is False
    assert api_model.token_env == GITHUB_TOKEN_ENV
    assert spec.kinds == ()
    assert spec.form_kind == "none"
    assert spec.opaque_credential is True
    assert spec.env_var_default == GITHUB_TOKEN_ENV

    app_source = (ROOT / "src/openbiliclaw/api/app.py").read_text(encoding="utf-8")
    github_block = app_source.split('github_data = sources_data.get("github")', 1)[1].split(
        'linuxdo_data = sources_data.get("linuxdo")',
        1,
    )[0]
    assert '"access_token_set",  # read-only GET echo; ignored' in github_block
    assert 'if "access_token" in github_data' in github_block
    assert 'github_cfg.access_token = ""' in github_block
    assert 'LIVE_PROBES.clear("github")' in github_block


def test_desktop_and_setup_expose_the_complete_github_controls() -> None:
    desktop_html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    desktop_js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(
        encoding="utf-8"
    )
    setup = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'data-source-status="github"' in desktop_html
    assert 'data-source-credential="github"' in desktop_html
    for element_id in (
        "githubEnabled",
        "githubUsername",
        "githubAccessToken",
        "githubClearToken",
        "githubModeSearch",
        "githubModeRanked",
        "githubModeLatest",
        "githubDailySearchBudget",
        "githubDailyRankedBudget",
        "githubDailyLatestBudget",
        "shareGitHub",
    ):
        assert f'id="{element_id}"' in desktop_html
        assert f'"{element_id}"' in desktop_js

    assert 'id="githubAccessToken" type="password"' in desktop_html
    assert "config.sources?.github?.access_token_set" in desktop_js
    assert 'document.getElementById("githubClearToken")?.checked' in desktop_js
    assert '? { access_token: "" }' in desktop_js
    assert 'sourceDateFieldsForUpdate("github")' in desktop_js
    assert '"github", "zhihu"' in desktop_js
    assert 'urlHostMatches(url, ["github.com"])' in desktop_js
    assert '"repository"' in desktop_js
    assert "近 30 天创建，按最近更新排序" in desktop_html

    assert "INIT_SOURCE_OPTIONS = SourceStatus.INIT_SOURCE_KEYS" in setup
    assert 'input[data-init-source="github"]' in setup
    assert 'githubTokenInput.id = "initGitHubToken"' in setup
    assert 'githubTokenInput.type = "password"' in setup
    assert "if (githubToken) github.access_token = githubToken;" in setup
    assert "currentConfig?.sources?.github?.username" in setup
    assert "currentConfig?.sources?.github?.access_token" not in setup
    for reason in (
        "github_identity_required",
        "github_identity_not_found",
        "github_token_rejected",
        "github_identity_mismatch",
        "github_bootstrap_timeout",
        "github_bootstrap_failed",
        "github_partial",
    ):
        assert f"{reason}:" in desktop_js
        assert f"{reason}:" in setup


def test_popup_and_mobile_keep_github_host_text_card_and_date_parity() -> None:
    popup_html = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")
    popup_js = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")
    popup_helpers = (ROOT / "extension/popup/popup-helpers.js").read_text(encoding="utf-8")
    mobile_models = (ROOT / "src/openbiliclaw/web/js/view-models.js").read_text(encoding="utf-8")
    mobile_launch = (ROOT / "src/openbiliclaw/web/js/app-launch.js").read_text(encoding="utf-8")
    mobile_saved = (ROOT / "src/openbiliclaw/web/js/views/saved.js").read_text(encoding="utf-8")
    mobile_history = (ROOT / "src/openbiliclaw/web/js/views/history.js").read_text(encoding="utf-8")

    for element_id in (
        "cfgGithubAccessToken",
        "cfgGithubClearToken",
        "cfgGithubModeSearch",
        "cfgGithubModeRanked",
        "cfgGithubModeLatest",
        "cfgGithubDailySearchBudget",
        "cfgGithubDailyRankedBudget",
        "cfgGithubDailyLatestBudget",
        "cfgPoolShareGithub",
    ):
        assert f'id="{element_id}"' in popup_html
        assert f'"{element_id}"' in popup_js
    assert 'checked("cfgGithubClearToken")' in popup_js
    assert '? { access_token: "" }' in popup_js
    assert 'popupSourceDateFieldsForUpdate("github")' in popup_js
    assert "近 30 天创建，按最近更新" in popup_html

    for source in (popup_helpers, mobile_models):
        assert 'gh: "github"' in source
        assert 'github: "GitHub"' in source
        assert '"repository"' in source
        assert '"github.com"' in source
        assert 'platform === "github"' in source

    assert 'hostMatches(host, "github.com")' in mobile_launch
    assert 'return "";' in mobile_launch
    assert 'github: "GitHub"' in mobile_saved
    assert 'github: "GitHub"' in mobile_history


def test_extension_manifests_grant_no_github_host_script_task_or_cookie_scope() -> None:
    for manifest_name in ("manifest.json", "manifest.firefox.json"):
        manifest_path = ROOT / "extension" / manifest_name
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        explicit_hosts = [
            *manifest.get("host_permissions", []),
            *manifest.get("optional_host_permissions", []),
        ]
        content_scripts = manifest.get("content_scripts", [])
        content_matches = [match for entry in content_scripts for match in entry.get("matches", [])]
        content_assets = [asset for entry in content_scripts for asset in entry.get("js", [])]
        resource_matches = [
            match
            for entry in manifest.get("web_accessible_resources", [])
            for match in entry.get("matches", [])
        ]

        # The generic cookies permission predates GitHub and serves other
        # adapters. With no GitHub host grant, content script, or resource
        # match, this source receives no extension cookie/task capability.
        assert not any("github.com" in value for value in explicit_hosts)
        assert not any("github.com" in value for value in content_matches)
        assert not any("github.com" in value for value in resource_matches)
        assert not any("github" in asset.casefold() for asset in content_assets)
        assert not any("github" in permission.casefold() for permission in manifest["permissions"])

    extension_runtime = ROOT / "extension/src"
    github_runtime_paths = [
        path.relative_to(extension_runtime)
        for path in extension_runtime.rglob("*")
        if path.is_file() and "github" in path.name.casefold()
    ]
    assert github_runtime_paths == []
