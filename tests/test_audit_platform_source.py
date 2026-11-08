"""Contract tests for the platform-source registration inventory."""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_platform_source.py"


def _dynamic_inventory(tmp_path: Path, *, replacements: dict[str, tuple[str, str]] | None = None):
    """Copy only the portable registry seams, without legacy fixed-slug evidence."""
    module = runpy.run_path(str(SCRIPT))
    contract = module["load_contract"](ROOT / "docs/platform-source-contract.dynamic-web.toml")
    files = (
        "src/openbiliclaw/sources/platforms.py",
        "src/openbiliclaw/sources/custom_sources.py",
        "src/openbiliclaw/sources/workflow_service.py",
        "src/openbiliclaw/sources/workflow_store.py",
        "src/openbiliclaw/sources/workflow_executors.py",
        "src/openbiliclaw/sources/web_recipe.py",
        "src/openbiliclaw/runtime/custom_workflow_producer.py",
        "src/openbiliclaw/runtime/keyword_planner.py",
        "src/openbiliclaw/runtime/inspiration_pipeline.py",
        "src/openbiliclaw/runtime/refresh.py",
        "src/openbiliclaw/api/runtime_context.py",
        "src/openbiliclaw/api/custom_sources.py",
        "tests/test_custom_source_runtime.py",
        "tests/test_custom_source_contract.py",
    )
    for relative in files:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        if relative in (replacements or {}):
            old, new = replacements[relative]
            content = target.read_text(encoding="utf-8")
            assert old in content
            target.write_text(content.replace(old, new), encoding="utf-8")
    return module["Inventory"](tmp_path, contract)


def test_dynamic_workflow_audit_uses_declared_instance_registry_without_fixed_slug(
    tmp_path: Path,
) -> None:
    inventory = _dynamic_inventory(tmp_path)
    results = [*inventory._source_auth(), *inventory._discover(), *inventory._search()]
    assert all(result.status == "PASS" for result in results), [
        (result.capability, result.detail) for result in results if result.status != "PASS"
    ]
    assert "src/openbiliclaw/runtime/custom_workflow_producer.py" in inventory._producer_files()
    assert not (tmp_path / "src/openbiliclaw/api/source_auth/verify.py").exists()


def test_dynamic_workflow_audit_reports_removed_claim_and_resume_even_with_legacy_web(
    tmp_path: Path,
) -> None:
    inventory = _dynamic_inventory(
        tmp_path,
        replacements={
            "src/openbiliclaw/runtime/custom_workflow_producer.py": (
                "coordinator.claim(",
                "coordinator.other(",
            ),
            "src/openbiliclaw/api/custom_sources.py": (
                '@router.post("/drafts/{draft_id}/resume")',
                '@router.post("/unrelated")',
            ),
        },
    )
    _write(
        tmp_path,
        "src/openbiliclaw/api/source_auth/verify.py",
        'VERIFY_ACTIONS = {"web": "live_probe"}\n',
    )
    _write(tmp_path, "src/openbiliclaw/runtime/web_producer.py", 'coordinator.claim("web")\n')
    results = {
        result.capability: result for result in [*inventory._source_auth(), *inventory._search()]
    }
    assert results["source-auth.verify"].status == "MISSING"
    assert results["search.keyword-claim"].status == "MISSING"


def test_dynamic_workflow_audit_does_not_hide_missing_instance_planner(
    tmp_path: Path,
) -> None:
    inventory = _dynamic_inventory(
        tmp_path,
        replacements={
            "src/openbiliclaw/runtime/keyword_planner.py": (
                "return (*_PLANNER_PLATFORMS, *self._custom_keyword_sources())",
                "return _PLANNER_PLATFORMS",
            ),
        },
    )
    results = {result.capability: result for result in inventory._search()}
    assert results["search.planner"].status == "MISSING"


def test_dynamic_surface_audit_does_not_accept_an_unrelated_web_login_link(tmp_path: Path) -> None:
    inventory = _dynamic_inventory(tmp_path)
    _write(tmp_path, "src/openbiliclaw/web/setup/index.html", '<a href="/web">登录后重试</a>')
    result = inventory._surface_result("setup")
    assert result.status == "MISSING"
    assert "explicit dynamic-source setup entry" in result.detail


CONTRACT = """\
schema_version = 1
canonical_slug = "example"
display_name = "Example"
integration_level = "discovery-only"
aliases = ["ex"]
hosts = ["example.test"]
content_types = ["article"]

[transport]
kind = "official-api"
owner = "backend"
entrypoints = ["src/openbiliclaw/sources/example_client.py"]
route_aliases = []
fallback_owner = "none"
requires_overseas_network = false
routed_by_network_mode = false
network_policy = "Backend client uses direct trust_env=false transport."

[transport.capability_routes]
discover = "backend:anonymous"

[identity]
item_id = "example:<article_id>"
url = "https://example.test/articles/<article_id>"
dedupe = "canonical family plus article id"
account_scope = "public catalog only"

[auth]
mode = "anonymous"
credential_kinds = []
verify_action = "none"
write_path = "none"
account_resolution = "no account resolution"
identity_evidence = "no account identity is claimed"
login_cookie_names = []
login_state_path = "none"

[auth.capability_modes]
discover = "anonymous"

[auth.capability_required]
discover = true

[upstream]
success_content_types = ["application/json"]
pagination = "opaque page cursor with one bounded reset"
terminal_evidence = "valid JSON envelope explicitly reports an empty page"
terminal_policy = "Only a valid empty envelope is empty."
partial_policy = "Retain accepted rows and report degraded."
publication_time_policy = "Use only authoritative upstream timestamps."

[discover]
modes = ["feed"]
search_generation = "none; feed-only source"
budget = "daily feed budget charged after final retention"
cursor = "persisted opaque feed cursor with bounded reset"

[profile]
signals = false
incremental = false
refresh_mode = "none"

[extension]
task = "none"
hosts = []
task_marker = false
background = false
early_response = false
cookie_sync = false

[surfaces]
cli = true
setup = true
desktop = true
mobile = true
extension_popup = true
source_status = true
credentials = true
recommendation = true

[engagement]
view = "unavailable"
like = "unavailable"
favorite = "unavailable"
comment = "unavailable"
share = "unavailable"
danmaku = "unavailable"

[media]
image = "none"
image_hosts = []
deep_link = "browser-fallback"
native_save = false

[e2e]
safe_actions = ["feed"]
mutating_actions = []

[e2e.safe_assertions]
feed = "upstream-state-unchanged"

[e2e.safe_postconditions]
feed = "A bounded public feed GET returns fixture rows and leaves upstream account state unchanged."

[events]
strategy_prefixes = ["example-", "example_"]
mappings = "Discovery rows map to canonical article candidates."
scope_caps = "Backend freezes one bounded feed scope."

[task]
lease = "No extension task; backend request timeout owns the lease boundary."
idle_deadline = "No extension task; request timeout is the idle boundary."
absolute_deadline = "One bounded feed request."
retry = "At most one retry before degraded."
buffer = "No browser response buffer."

[smoke]
storage_scope = "isolated-only"

[smoke.sinks]
task = "forbidden"
task_result = "forbidden"
seen = "forbidden"
affinity = "forbidden"
snapshot = "forbidden"
schedule = "forbidden"
event_ingress = "forbidden"
memory = "forbidden"
profile = "forbidden"

[exclusions]
"profile.signals" = "This source is discovery-only."
"profile.incremental" = "No profile signals exist to refresh."
"profile.refresh-mode" = "No profile refresh exists for a discovery-only source."
"search.integration" = "The fixture is deliberately feed-only."
"extension.task" = "The official API owns transport; no browser task is needed."
"extension.task-marker" = "No browser task exists, so no task marker is needed."
"extension.background" = "No extension task or callback exists."
"extension.early-response" = "No browser response is captured."
"extension.cookie-sync" = "The anonymous API does not consume browser login state."
"engagement.view" = "The upstream schema has no view count."
"engagement.like" = "The upstream schema has no like count."
"engagement.favorite" = "The upstream schema has no favorite count."
"engagement.comment" = "The upstream schema has no comment count."
"engagement.share" = "The upstream schema has no share count."
"engagement.danmaku" = "The platform has no danmaku concept."
"media.image" = "The source has no cover images."
"media.deep-link" = "The source deliberately opens its canonical web URL."
"media.native-save" = "The integration is read-only."

[exclusion_tests]
"search.integration" = ["tests/test_example_contract.py::test_search_integration_exclusion"]
"profile.signals" = ["tests/test_example_contract.py::test_profile_capability_exclusions"]
"profile.incremental" = ["tests/test_example_contract.py::test_profile_capability_exclusions"]
"profile.refresh-mode" = ["tests/test_example_contract.py::test_profile_capability_exclusions"]
"extension.task" = ["tests/test_example_contract.py::test_extension_exclusions"]
"extension.task-marker" = ["tests/test_example_contract.py::test_extension_exclusions"]
"extension.background" = ["tests/test_example_contract.py::test_extension_exclusions"]
"extension.early-response" = ["tests/test_example_contract.py::test_extension_exclusions"]
"extension.cookie-sync" = ["tests/test_example_contract.py::test_extension_exclusions"]
"engagement.view" = ["tests/test_example_contract.py::test_engagement_capability_exclusions"]
"engagement.like" = ["tests/test_example_contract.py::test_engagement_capability_exclusions"]
"engagement.favorite" = ["tests/test_example_contract.py::test_engagement_capability_exclusions"]
"engagement.comment" = ["tests/test_example_contract.py::test_engagement_capability_exclusions"]
"engagement.share" = ["tests/test_example_contract.py::test_engagement_capability_exclusions"]
"engagement.danmaku" = ["tests/test_example_contract.py::test_engagement_capability_exclusions"]
"media.image" = ["tests/test_example_contract.py::test_media_capability_exclusions"]
"media.deep-link" = ["tests/test_example_contract.py::test_media_capability_exclusions"]
"media.native-save" = ["tests/test_example_contract.py::test_media_capability_exclusions"]
"""


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _without_exclusions(text: str, *keys: str) -> str:
    prefixes = tuple(f'"{key}" =' for key in keys)
    return "".join(line for line in text.splitlines(keepends=True) if not line.startswith(prefixes))


def _recipe_allowlist_contract(text: str = CONTRACT) -> str:
    return (
        text.replace("schema_version = 1", "schema_version = 2", 1)
        .replace(
            'integration_level = "discovery-only"\n',
            'integration_level = "discovery-only"\nhost_policy = "recipe-allowlist"\n',
            1,
        )
        .replace('hosts = ["example.test"]', "hosts = []", 1)
        .replace('kind = "official-api"', 'kind = "web-http"', 1)
    )


def _fixture_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    files = {
        "pyproject.toml": "[project]\nname='fixture'\nversion='0'\n",
        "config.example.toml": "[sources.example]\nenabled = false\n",
        "src/openbiliclaw/sources/platforms.py": (
            'PLATFORM_EXAMPLE = "example"\n'
            "SOURCE_FAMILY_RULES = (SourceFamilyRule(\n"
            "    family=PLATFORM_EXAMPLE,\n"
            '    platform_aliases=frozenset({"example", "ex"}),\n'
            '    source_prefixes=("example-", "example_"),\n'
            '    url_hosts=("example.test",),\n'
            "),)\n"
        ),
        "src/openbiliclaw/sources/example_client.py": (
            'PLATFORM = "example"\nCONTENT_TYPES = ("article",)\n'
        ),
        "src/openbiliclaw/config.py": "class SourcesConfig:\n    example: object\n",
        "src/openbiliclaw/api/models.py": (
            "class SourcesStatusResponse:\n    example: object\n"
            "class SourcesCredentialsResponse:\n    example: object\n"
            "class SourcesConfigOut:\n    example: object\n"
        ),
        "src/openbiliclaw/api/app.py": (
            '@app.get("/api/sources/status")\n'
            "def status():\n"
            "    items = {}\n"
            "    for slug, provider in SOURCE_AUTH_PROVIDERS.items():\n"
            "        items[slug] = provider()\n"
            "    return SourcesStatusResponse(**items)\n"
            '@app.get("/api/sources/credentials")\n'
            "def credentials(): return SourcesCredentialsResponse(example=object())\n"
            '@app.put("/api/config")\n'
            "def config(): return SourcesConfigOut(example=object())\n"
        ),
        "src/openbiliclaw/api/runtime_context.py": 'PRODUCERS = {"example": object()}\n',
        "src/openbiliclaw/api/source_auth/providers.py": (
            'SOURCE_AUTH_PROVIDERS = {"example": object()}\n'
        ),
        "src/openbiliclaw/api/source_auth/verify.py": 'VERIFY_ACTIONS = {"example": "none"}\n',
        "src/openbiliclaw/api/source_auth/write.py": 'CREDENTIAL_SPECS = {"example": object()}\n',
        "src/openbiliclaw/runtime/source_policy.py": (
            'SOURCE_ORDER = ("example",)\n'
            'DEFAULT_SOURCE_ENABLED = {"example": False}\n'
            'DEFAULT_POOL_SOURCE_SHARES = {"example": 1}\n'
        ),
        "src/openbiliclaw/runtime/example_producer.py": (
            'PLATFORM = "example"\nMODES = ("feed",)\n'
        ),
        "src/openbiliclaw/runtime/refresh.py": 'PRODUCERS = {"example": "tick"}\n',
        "src/openbiliclaw/web/shared/source-status.js": (
            'const SOURCE_KEYS = Object.freeze(["example"]);\n'
            'const SOURCE_LABELS = Object.freeze({example: "Example"});\n'
        ),
        "src/openbiliclaw/cli.py": '@app.command("fetch-example")\ndef fetch_example(): pass\n',
        "src/openbiliclaw/web/setup/index.html": '<input data-init-source="example">\n',
        "src/openbiliclaw/web/desktop/index.html": '<div data-source="example"></div>\n',
        "src/openbiliclaw/web/desktop/assets/js/app.js": 'const source = "example";\n',
        "src/openbiliclaw/web/js/view-models.js": 'const source = "example";\n',
        "extension/popup/popup.html": '<div data-source="example"></div>\n',
        "extension/popup/popup-helpers.js": 'const source = "example";\n',
        "tests/test_example.py": (
            'def test_example_feed_registration():\n    assert "example" == "example"\n'
        ),
        "tests/test_example_contract.py": (
            "def test_search_integration_exclusion():\n"
            "    assert 'search' not in {'feed'}\n\n"
            "def test_profile_capability_exclusions():\n"
            "    assert {'signals': False, 'incremental': False, 'refresh_mode': 'none'}\n\n"
            "def test_extension_exclusions():\n"
            "    assert {'task': 'none', 'marker': False, 'background': False, "
            "'early_response': False, 'cookie_sync': False}\n\n"
            "def test_engagement_capability_exclusions():\n"
            "    assert {'view', 'like', 'favorite', 'comment', 'share', 'danmaku'}\n\n"
            "def test_media_capability_exclusions():\n"
            "    assert {'image': 'none', 'deep_link': 'browser-fallback', 'native_save': False}\n"
        ),
        "docs/modules/example.md": "# Example\n\nDiscovery-only source.\n",
        "docs/changelog.md": "- Add Example platform source.\n",
    }
    for relative, text in files.items():
        _write(repo, relative, text)
    contract = repo / "docs/platform-source-contract.toml"
    _write(repo, contract.relative_to(repo).as_posix(), CONTRACT)
    return repo, contract


def _run(contract: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--contract", str(contract), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    value: Any = json.loads(result.stdout)
    assert isinstance(value, dict)
    return cast("dict[str, Any]", value)


def test_complete_fixture_passes_registration_check_but_keeps_manual_open(
    tmp_path: Path,
) -> None:
    _repo, contract = _fixture_repo(tmp_path)

    result = _run(contract, "--check", "--json")

    assert result.returncode == 0, result.stderr or result.stdout
    payload = _payload(result)
    assert payload["tool"] == "platform-source-registration-inventory"
    assert payload["contract"]["schema_version"] == 1
    assert "host_policy" not in payload["contract"]
    assert payload["summary"]["required_missing"] == 0
    assert payload["summary"]["registration_check_passed"] is True
    assert payload["summary"]["MANUAL"] > 0
    assert payload["summary"]["fully_verified"] is False
    assert "does not prove semantic correctness" in payload["disclaimer"]
    assert any(item["status"] == "N/A" for item in payload["results"])
    assert all(
        item["detail"].startswith("contract explicitly declares")
        for item in payload["results"]
        if item["status"] == "N/A"
    )
    assert all(item["evidence"] for item in payload["results"] if item["status"] == "N/A")
    action_boundary = next(
        item for item in payload["results"] if item["capability"] == "e2e.action-boundary"
    )
    assert "safe_postconditions={'feed':" in action_boundary["detail"]
    assert any(
        item["capability"] == "engagement.branch-coverage" and item["status"] == "MANUAL"
        for item in payload["results"]
    )
    for item in payload["results"]:
        if item["status"] != "PASS":
            continue
        assert item["evidence"], item["capability"]
        assert all(evidence["path"] and evidence["line"] >= 1 for evidence in item["evidence"])


def test_schema_v2_recipe_allowlist_accepts_empty_hosts_and_web_http(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path)
    contract.write_text(_recipe_allowlist_contract(), encoding="utf-8")
    platforms = repo / "src/openbiliclaw/sources/platforms.py"
    platforms.write_text(
        platforms.read_text(encoding="utf-8").replace(
            '    url_hosts=("example.test",),\n',
            "",
        ),
        encoding="utf-8",
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 0, result.stderr or result.stdout
    payload = _payload(result)
    assert payload["contract"]["schema_version"] == 2
    assert payload["contract"]["host_policy"] == "recipe-allowlist"
    assert payload["contract"]["hosts"] == []
    assert payload["contract"]["transport"]["kind"] == "web-http"
    canonical = next(
        item for item in payload["results"] if item["capability"] == "canonical.registry"
    )
    assert canonical["status"] == "PASS"


def test_schema_v1_keeps_static_hosts_and_rejects_web_http(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path / "empty-hosts")
    contract.write_text(
        CONTRACT.replace('hosts = ["example.test"]', "hosts = []", 1),
        encoding="utf-8",
    )

    empty_hosts = _run(contract, "--json")

    assert empty_hosts.returncode == 2
    assert "contract.hosts must not be empty" in empty_hosts.stderr

    _repo, contract = _fixture_repo(tmp_path / "web-http")
    contract.write_text(
        CONTRACT.replace('kind = "official-api"', 'kind = "web-http"', 1),
        encoding="utf-8",
    )

    web_http = _run(contract, "--json")

    assert web_http.returncode == 2
    assert "transport.kind='web-http' requires schema_version=2" in web_http.stderr


def test_schema_v2_recipe_allowlist_rejects_static_contract_or_registry_hosts(
    tmp_path: Path,
) -> None:
    _repo, contract = _fixture_repo(tmp_path / "contract-host")
    contract.write_text(
        _recipe_allowlist_contract().replace("hosts = []", 'hosts = ["example.test"]', 1),
        encoding="utf-8",
    )

    static_contract_host = _run(contract, "--json")

    assert static_contract_host.returncode == 2
    assert "host_policy='recipe-allowlist' requires hosts=[]" in static_contract_host.stderr

    _repo, contract = _fixture_repo(tmp_path / "registry-host")
    contract.write_text(_recipe_allowlist_contract(), encoding="utf-8")

    static_registry_host = _run(contract, "--check", "--json")

    assert static_registry_host.returncode == 1
    canonical = next(
        item
        for item in _payload(static_registry_host)["results"]
        if item["capability"] == "canonical.registry"
    )
    assert canonical["status"] == "MISSING"
    assert "recipe-allowlist family has no static url_hosts" in canonical["detail"]


def test_schema_v2_requires_a_known_host_policy(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path / "missing")
    contract.write_text(
        CONTRACT.replace("schema_version = 1", "schema_version = 2", 1),
        encoding="utf-8",
    )

    missing = _run(contract, "--json")

    assert missing.returncode == 2
    assert "host_policy must be a non-empty string" in missing.stderr

    _repo, contract = _fixture_repo(tmp_path / "unknown")
    contract.write_text(
        _recipe_allowlist_contract().replace(
            'host_policy = "recipe-allowlist"',
            'host_policy = "wildcard"',
            1,
        ),
        encoding="utf-8",
    )

    unknown = _run(contract, "--json")

    assert unknown.returncode == 2
    assert "host_policy must be one of" in unknown.stderr


def test_generic_status_route_does_not_substitute_for_concrete_source_roster(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path)
    _write(
        repo,
        "src/openbiliclaw/web/shared/source-status.js",
        "const SOURCE_KEYS = Object.freeze([]);\nconst SOURCE_LABELS = Object.freeze({});\n",
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    payload = _payload(result)
    missing = {item["capability"] for item in payload["results"] if item["status"] == "MISSING"}
    assert "shared.source-keys" in missing
    assert "surface.source_status" in missing


def test_search_mode_activates_planner_prompt_claim_and_provenance_gates(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path)
    contract.write_text(
        _without_exclusions(
            CONTRACT.replace('modes = ["feed"]', 'modes = ["search"]'),
            "search.integration",
        ),
        encoding="utf-8",
    )
    _write(
        repo,
        "src/openbiliclaw/runtime/example_producer.py",
        'PLATFORM = "example"\nMODES = ("search",)\n',
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    payload = _payload(result)
    missing = {item["capability"] for item in payload["results"] if item["status"] == "MISSING"}
    assert {
        "search.planner",
        "search.prompts",
        "search.keyword-claim",
        "search.provenance-tests",
        "search.inspiration-axis",
    } <= missing


def test_disabled_capability_requires_an_explicit_exclusion_reason(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path)
    contract.write_text(
        CONTRACT.replace('"media.native-save" = "The integration is read-only."\n', ""),
        encoding="utf-8",
    )

    result = _run(contract, "--json")

    assert result.returncode == 2
    assert "add a non-empty [exclusions] reason for media.native-save" in result.stderr


def test_diff_base_annotates_evidence_without_scoping_out_unchanged_registrations(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path)
    commands = (
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "fixture baseline",
        ],
    )
    for command in commands:
        subprocess.run(command, cwd=repo, check=True, capture_output=True, text=True)
    config = repo / "src/openbiliclaw/config.py"
    config.write_text(config.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

    result = _run(contract, "--check", "--json", "--diff-base", "HEAD")

    assert result.returncode == 0, result.stderr or result.stdout
    payload = _payload(result)
    config_result = next(
        item for item in payload["results"] if item["capability"] == "config.registration"
    )
    changed = {item["path"]: item["changed_since_base"] for item in config_result["evidence"]}
    assert changed["src/openbiliclaw/config.py"] is True
    assert changed["config.example.toml"] is False
    assert payload["summary"]["required_missing"] == 0


def test_family_aliases_hosts_and_network_flags_must_share_the_slug_rule(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path)
    platforms = repo / "src/openbiliclaw/sources/platforms.py"
    platforms.write_text(
        platforms.read_text(encoding="utf-8").replace(
            "),)\n",
            "),\n"
            "SourceFamilyRule(\n"
            '    family="other",\n'
            '    platform_aliases=frozenset({"bili"}),\n'
            '    url_hosts=("reddit.com",),\n'
            "    requires_overseas_network=True,\n"
            "),)\n",
        ),
        encoding="utf-8",
    )
    contract.write_text(
        CONTRACT.replace('aliases = ["ex"]', 'aliases = ["bili"]')
        .replace('hosts = ["example.test"]', 'hosts = ["reddit.com"]', 1)
        .replace("requires_overseas_network = false", "requires_overseas_network = true"),
        encoding="utf-8",
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 2
    assert "must belong only to SourceFamilyRule family='example'" in result.stderr
    assert "owners=['other']" in result.stderr


def test_verify_action_requires_an_enum_and_exact_verify_actions_value(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    _write(
        repo,
        "src/openbiliclaw/api/source_auth/verify.py",
        'VERIFY_ACTIONS = {"example": "live_probe"}\n',
    )

    mismatch = _run(contract, "--check", "--json")

    assert mismatch.returncode == 1
    verify = next(
        item for item in _payload(mismatch)["results"] if item["capability"] == "source-auth.verify"
    )
    assert verify["status"] == "MISSING"
    assert "VERIFY_ACTIONS exact action" in verify["detail"]

    contract.write_text(
        CONTRACT.replace('verify_action = "none"', 'verify_action = "looks_plausible"'),
        encoding="utf-8",
    )
    invalid = _run(contract, "--json")
    assert invalid.returncode == 2
    assert "auth.verify_action must be one of" in invalid.stderr


def test_short_alias_does_not_discover_unrelated_xhs_files_or_tests(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    contract.write_text(CONTRACT.replace('aliases = ["ex"]', 'aliases = ["x"]'), encoding="utf-8")
    platforms = repo / "src/openbiliclaw/sources/platforms.py"
    platforms.write_text(
        platforms.read_text(encoding="utf-8").replace('"example", "ex"', '"example", "x"'),
        encoding="utf-8",
    )
    (repo / "src/openbiliclaw/runtime/example_producer.py").unlink()
    (repo / "tests/test_example.py").unlink()
    (repo / "tests/test_example_contract.py").unlink()
    _write(
        repo,
        "src/openbiliclaw/runtime/xhs_producer.py",
        'PLATFORM = "x"\nMODES = ("feed",)\n',
    )
    _write(repo, "tests/test_xhs.py", "def test_xhs_behavior():\n    assert True\n")

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    missing = {
        item["capability"] for item in _payload(result)["results"] if item["status"] == "MISSING"
    }
    assert "discover.formal" in missing
    assert "tests.source-specific" in missing


def test_source_specific_test_requires_collected_asserting_test_node(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path / "empty")
    _write(repo, "tests/test_example.py", "")
    _write(repo, "tests/test_example_contract.py", "# no collected tests\n")

    empty = _run(contract, "--check", "--json")

    assert empty.returncode == 1
    row = next(
        item for item in _payload(empty)["results"] if item["capability"] == "tests.source-specific"
    )
    assert row["status"] == "MISSING"

    repo, contract = _fixture_repo(tmp_path / "helper")
    _write(repo, "tests/test_example.py", "def helper():\n    assert True\n")
    _write(repo, "tests/test_example_contract.py", "def helper_too():\n    assert True\n")

    helper_only = _run(contract, "--check", "--json")

    assert helper_only.returncode == 1
    row = next(
        item
        for item in _payload(helper_only)["results"]
        if item["capability"] == "tests.source-specific"
    )
    assert row["status"] == "MISSING"


def _enable_foreground_browser_task(repo: Path, contract: Path, route: str) -> None:
    contract.write_text(
        CONTRACT.replace("route_aliases = []", f'route_aliases = ["{route}"]')
        .replace(
            'discover = "backend:anonymous"',
            'discover = "extension:browser-task:anonymous"',
        )
        .replace('task = "none"', 'task = "browser-task"')
        .replace('task = "forbidden"', 'task = "allowed"', 1)
        .replace('task_result = "forbidden"', 'task_result = "allowed"', 1)
        .replace(
            '"extension.task" = "The official API owns transport; no browser task is needed."\n',
            "",
        )
        .replace(
            '"extension.task" = ["tests/test_example_contract.py::test_extension_exclusions"]\n',
            "",
        )
        .replace("hosts = []", 'hosts = ["example.test"]', 1)
        .replace(
            '"extension.background" = "No extension task or callback exists."',
            '"extension.background" = "The foreground executor restores the active tab; '
            'no background worker is needed."',
        ),
        encoding="utf-8",
    )
    if route != "ex":
        platforms = repo / "src/openbiliclaw/sources/platforms.py"
        platforms.write_text(
            platforms.read_text(encoding="utf-8").replace(
                'frozenset({"example", "ex"})',
                f'frozenset({{"example", "ex", "{route}"}})',
            ),
            encoding="utf-8",
        )
    _write(repo, f"extension/src/content/{route}.ts", f'const source = "{route}";\n')
    _write(repo, f"extension/src/content/{route}/task-executor.ts", "export {};\n")
    _write(repo, "extension/scripts/build.mjs", f'const source = "{route}";\n')
    _write(repo, "extension/manifest.json", f'{{"source":"{route}","host":"example.test"}}\n')
    _write(
        repo,
        "extension/manifest.firefox.json",
        f'{{"source":"{route}","host":"example.test"}}\n',
    )
    app = repo / "src/openbiliclaw/api/app.py"
    app.write_text(
        app.read_text(encoding="utf-8") + f'\n@app.get("/api/sources/{route}/next-task")\n'
        f"def {route.replace('-', '_')}_next_task(): return None\n"
        f'@app.post("/api/sources/{route}/task-result")\n'
        f"def {route.replace('-', '_')}_task_result(): return None\n"
        f'@app.post("/api/sources/{route}/kick")\n'
        f"def {route.replace('-', '_')}_kick(): return None\n",
        encoding="utf-8",
    )


def test_browser_task_may_run_foreground_and_uses_explicit_route_alias(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    _enable_foreground_browser_task(repo, contract, "ex")
    _write(
        repo,
        "extension/tests/ex-task.test.ts",
        'import assert from "node:assert/strict";\n'
        'test("example task", () => { assert.equal(1, 1); });\n',
    )

    result = _run(contract, "--json")

    assert result.returncode == 0, result.stderr
    payload = _payload(result)
    extension = next(item for item in payload["results"] if item["capability"] == "extension.task")
    background = next(
        item for item in payload["results"] if item["capability"] == "extension.background"
    )
    assert extension["status"] == "PASS"
    assert background["status"] == "N/A"
    assert "foreground executor restores the active tab" in background["detail"]


def test_short_route_alias_does_not_borrow_an_xhs_extension_test(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    _enable_foreground_browser_task(repo, contract, "x")
    _write(repo, "extension/tests/xhs-task.test.ts", 'test("xhs task", () => {});\n')

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    extension = next(
        item for item in _payload(result)["results"] if item["capability"] == "extension.task"
    )
    assert extension["status"] == "MISSING"
    assert "source-specific extension regression" in extension["detail"]


def test_extension_task_test_requires_an_assertion(tmp_path: Path) -> None:
    cases = (
        'import assert from "node:assert/strict";\n'
        'test("example task", () => {});\n// assert.equal(1, 1)\n',
        'import assert from "node:assert/strict";\n'
        'test("example task", () => {});\nfunction helper() { assert.equal(1, 1); }\n',
        'import assert from "node:assert/strict";\ntest("assert.equal(fake)", () => {});\n',
        'import assert from "node:assert/strict";\n'
        'test("example task", () => { function assert() {} });\n',
    )
    for index, source in enumerate(cases):
        repo, contract = _fixture_repo(tmp_path / str(index))
        _enable_foreground_browser_task(repo, contract, "ex")
        _write(repo, "extension/tests/ex-task.test.ts", source)

        result = _run(contract, "--check", "--json")

        assert result.returncode == 1
        extension = next(
            item for item in _payload(result)["results"] if item["capability"] == "extension.task"
        )
        assert extension["status"] == "MISSING"
        assert "source-specific extension regression" in extension["detail"]


def test_cookie_sync_is_independent_of_extension_task(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path)
    contract.write_text(
        _without_exclusions(
            CONTRACT.replace("hosts = []", 'hosts = ["example.test"]', 1)
            .replace("cookie_sync = false", "cookie_sync = true")
            .replace(
                'discover = "backend:anonymous"\n\n[identity]',
                'discover = "backend:anonymous"\ncookie-sync = "extension:anonymous"\n\n[identity]',
            )
            .replace(
                'discover = "anonymous"\n\n[auth.capability_required]',
                'discover = "anonymous"\ncookie-sync = "anonymous"\n\n[auth.capability_required]',
            )
            .replace(
                "discover = true\n\n[upstream]",
                "discover = true\ncookie-sync = true\n\n[upstream]",
            ),
            "extension.cookie-sync",
        ),
        encoding="utf-8",
    )

    result = _run(contract, "--json")

    assert result.returncode == 0, result.stderr
    cookie = next(
        item
        for item in _payload(result)["results"]
        if item["capability"] == "extension.cookie-sync"
    )
    assert cookie["status"] == "MISSING"


def test_unavailable_engagement_and_false_extension_features_require_reasons(
    tmp_path: Path,
) -> None:
    _repo, contract = _fixture_repo(tmp_path)
    contract.write_text(
        CONTRACT.replace('"engagement.view" = "The upstream schema has no view count."\n', ""),
        encoding="utf-8",
    )
    engagement = _run(contract, "--json")
    assert engagement.returncode == 2
    assert "engagement.view" in engagement.stderr

    second_root = tmp_path / "browser-task"
    repo, browser_contract = _fixture_repo(second_root)
    _enable_foreground_browser_task(repo, browser_contract, "ex")
    browser_contract.write_text(
        browser_contract.read_text(encoding="utf-8").replace(
            '"extension.background" = "The foreground executor restores the active tab; '
            'no background worker is needed."\n',
            "",
        ),
        encoding="utf-8",
    )
    background = _run(browser_contract, "--json")
    assert background.returncode == 2
    assert "extension.background" in background.stderr


def test_n_a_requires_a_resolvable_exact_contract_test(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path)
    contract.write_text(
        CONTRACT.replace(
            "tests/test_example_contract.py::test_search_integration_exclusion",
            "tests/test_example_contract.py::test_name_that_does_not_exist",
            1,
        ),
        encoding="utf-8",
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    search = next(
        item for item in _payload(result)["results"] if item["capability"] == "search.integration"
    )
    assert search["status"] == "MISSING"
    assert "resolvable exact test" in search["detail"]


def test_safe_actions_reject_known_mutators_and_native_save_requires_mutation_boundary(
    tmp_path: Path,
) -> None:
    _repo, contract = _fixture_repo(tmp_path)
    contract.write_text(
        CONTRACT.replace('safe_actions = ["feed"]', 'safe_actions = ["feed", "platform-like"]'),
        encoding="utf-8",
    )
    unsafe = _run(contract, "--json")
    assert unsafe.returncode == 2
    assert "known upstream mutators" in unsafe.stderr

    contract.write_text(
        CONTRACT.replace("native_save = false", "native_save = true"),
        encoding="utf-8",
    )
    native_save = _run(contract, "--json")
    assert native_save.returncode == 2
    assert "explicit save/platform-save entry" in native_save.stderr


def test_evidence_paths_and_diff_refs_fail_closed(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path)
    contract.write_text(
        CONTRACT.replace(
            "tests/test_example_contract.py::test_search_integration_exclusion",
            "../outside.py::test_example_feed_registration",
            1,
        ),
        encoding="utf-8",
    )
    unsafe_path = _run(contract, "--json")
    assert unsafe_path.returncode == 2
    assert "must stay inside the repository" in unsafe_path.stderr

    contract.write_text(CONTRACT, encoding="utf-8")
    option_like_ref = _run(contract, "--diff-base=--output=/tmp/nope", "--json")
    assert option_like_ref.returncode == 2
    assert "does not start with '-'" in option_like_ref.stderr


def test_safe_actions_are_fail_closed_and_require_exact_postconditions(tmp_path: Path) -> None:
    for index, action in enumerate(("watch-later", "star", "retweet", "点赞", "platform-observe")):
        _repo, contract = _fixture_repo(tmp_path / f"unsafe-{index}")
        contract.write_text(
            CONTRACT.replace('safe_actions = ["feed"]', f'safe_actions = ["{action}"]'),
            encoding="utf-8",
        )
        result = _run(contract, "--json")
        assert result.returncode == 2, (action, result.stderr)
        assert "safe_actions" in result.stderr

    _repo, contract = _fixture_repo(tmp_path / "postcondition")
    contract.write_text(
        CONTRACT.replace(
            "[e2e.safe_postconditions]\nfeed = ",
            "[e2e.safe_postconditions]\nsearch = ",
        ),
        encoding="utf-8",
    )
    result = _run(contract, "--json")
    assert result.returncode == 2
    assert "safe_postconditions must have exactly one" in result.stderr

    _repo, contract = _fixture_repo(tmp_path / "assertion-keys")
    contract.write_text(
        CONTRACT.replace(
            "[e2e.safe_assertions]\nfeed = ",
            "[e2e.safe_assertions]\nsearch = ",
        ),
        encoding="utf-8",
    )
    result = _run(contract, "--json")
    assert result.returncode == 2
    assert "safe_assertions must have exactly one" in result.stderr

    for index, assertion in enumerate(("account-state-may-change", "状态不变")):
        _repo, contract = _fixture_repo(tmp_path / f"assertion-value-{index}")
        contract.write_text(
            CONTRACT.replace("upstream-state-unchanged", assertion, 1),
            encoding="utf-8",
        )
        result = _run(contract, "--json")
        assert result.returncode == 2
        assert "must be exactly 'upstream-state-unchanged'" in result.stderr


def test_smoke_contract_classifies_every_projection_sink(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path / "missing")
    contract.write_text(CONTRACT.replace('profile = "forbidden"\n', "", 1), encoding="utf-8")

    missing = _run(contract, "--json")

    assert missing.returncode == 2
    assert "smoke.sinks must classify every projection sink" in missing.stderr

    _repo, contract = _fixture_repo(tmp_path / "invalid")
    contract.write_text(
        CONTRACT.replace('affinity = "forbidden"', 'affinity = "allowed-production"'),
        encoding="utf-8",
    )

    invalid = _run(contract, "--json")

    assert invalid.returncode == 2
    assert "smoke.sinks values must be allowed or forbidden" in invalid.stderr

    _repo, contract = _fixture_repo(tmp_path / "derived-write")
    contract.write_text(
        CONTRACT.replace('seen = "forbidden"', 'seen = "allowed"'),
        encoding="utf-8",
    )

    derived_write = _run(contract, "--json")

    assert derived_write.returncode == 2
    assert "derived projection sinks must be forbidden" in derived_write.stderr


def test_exclusion_tables_reject_stale_enabled_capability_entries(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path)
    contract.write_text(CONTRACT.replace('image = "none"', 'image = "direct"'), encoding="utf-8")

    stale = _run(contract, "--json")

    assert stale.returncode == 2
    assert "[exclusions] keys must exactly match" in stale.stderr
    assert "media.image" in stale.stderr


def test_route_aliases_reject_unsafe_foreign_and_symlink_paths(tmp_path: Path) -> None:
    for index, alias in enumerate(("../ex", "ex/path", "ex.py", "éx")):
        _repo, contract = _fixture_repo(tmp_path / f"route-{index}")
        contract.write_text(
            CONTRACT.replace("route_aliases = []", f'route_aliases = ["{alias}"]'),
            encoding="utf-8",
        )
        result = _run(contract, "--json")
        assert result.returncode == 2, (alias, result.stderr)
        assert "ASCII route keys" in result.stderr

    repo, contract = _fixture_repo(tmp_path / "foreign")
    platforms = repo / "src/openbiliclaw/sources/platforms.py"
    platforms.write_text(
        platforms.read_text(encoding="utf-8").replace(
            "),)\n",
            "),\n"
            "SourceFamilyRule(\n"
            '    family="reddit",\n'
            '    platform_aliases=frozenset({"reddit"}),\n'
            '    source_prefixes=("reddit-",),\n'
            '    url_hosts=("reddit.com",),\n'
            "),)\n",
        ),
        encoding="utf-8",
    )
    contract.write_text(
        CONTRACT.replace("route_aliases = []", 'route_aliases = ["reddit"]'),
        encoding="utf-8",
    )
    foreign = _run(contract, "--json")
    assert foreign.returncode == 2
    assert "owners=['reddit']" in foreign.stderr

    repo, contract = _fixture_repo(tmp_path / "symlink")
    outside = tmp_path / "outside.py"
    outside.write_text('PLATFORM = "example"\n', encoding="utf-8")
    entrypoint = repo / "src/openbiliclaw/sources/example_client.py"
    entrypoint.unlink()
    entrypoint.symlink_to(outside)
    escaped = _run(contract, "--json")
    assert escaped.returncode == 2
    assert "resolves outside the repository" in escaped.stderr


def test_host_permissions_and_transport_entrypoints_are_fail_closed(tmp_path: Path) -> None:
    for index, host in enumerate(("<all_urls>", "*.example.test", "localhost", "127.0.0.1")):
        _repo, contract = _fixture_repo(tmp_path / f"host-{index}")
        contract.write_text(
            CONTRACT.replace("hosts = []", f'hosts = ["{host}"]', 1),
            encoding="utf-8",
        )
        invalid = _run(contract, "--json")
        assert invalid.returncode == 2, (host, invalid.stderr)
        assert "extension.hosts" in invalid.stderr

    for index, host in enumerate(("com", "localhost", "nip.io", "sslip.io")):
        _repo, contract = _fixture_repo(tmp_path / f"image-host-{index}")
        contract.write_text(
            CONTRACT.replace('image = "none"', 'image = "proxy"').replace(
                "image_hosts = []", f'image_hosts = ["{host}"]'
            ),
            encoding="utf-8",
        )
        invalid_image = _run(contract, "--json")
        assert invalid_image.returncode == 2, (host, invalid_image.stderr)
        assert "media.image_hosts" in invalid_image.stderr

    repo, contract = _fixture_repo(tmp_path / "entrypoint")
    _write(repo, "config.toml", "OPENAI_API_KEY=sk-review-secret\n")
    contract.write_text(
        CONTRACT.replace(
            'entrypoints = ["src/openbiliclaw/sources/example_client.py"]',
            'entrypoints = ["config.toml"]',
        ),
        encoding="utf-8",
    )
    secret_file = _run(contract, "--json")
    assert secret_file.returncode == 2
    assert "implementation source files" in secret_file.stderr
    assert "sk-review-secret" not in secret_file.stdout + secret_file.stderr


def test_evidence_excerpts_redact_source_secret_assignments(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    client = repo / "src/openbiliclaw/sources/example_client.py"
    client.write_text(
        'API_KEY = "sk-review-secret"\nPLATFORM = "example"\nCONTENT_TYPES = ("article",)\n',
        encoding="utf-8",
    )

    result = _run(contract, "--json")

    assert result.returncode == 0
    assert "sk-review-secret" not in result.stdout
    assert "<source excerpt omitted>" in result.stdout


def test_exclusion_evidence_rejects_production_nested_skipped_and_irrelevant_nodes(
    tmp_path: Path,
) -> None:
    _repo, contract = _fixture_repo(tmp_path / "production")
    contract.write_text(
        CONTRACT.replace(
            "tests/test_example_contract.py::test_search_integration_exclusion",
            "src/openbiliclaw/runtime/example_producer.py::test_search_integration",
            1,
        ),
        encoding="utf-8",
    )
    production = _run(contract, "--json")
    assert production.returncode == 2
    assert "tests/ or extension/tests/" in production.stderr

    for variant, source, node_name in (
        (
            "nested",
            "def helper():\n"
            "    def test_example_search_integration_nested():\n"
            "        assert True\n",
            "test_example_search_integration_nested",
        ),
        (
            "skipped",
            "@pytest.mark.skip\ndef test_example_search_integration_skipped():\n    assert True\n",
            "test_example_search_integration_skipped",
        ),
        (
            "xfailed",
            (
                "@pytest.mark."
                + "xfail\ndef test_example_search_integration_xfailed():\n    assert True\n"
            ),
            "test_example_search_integration_xfailed",
        ),
        (
            "irrelevant",
            "def test_unrelated_contract():\n    assert 2 + 2 == 4\n",
            "test_unrelated_contract",
        ),
    ):
        repo, contract = _fixture_repo(tmp_path / variant)
        _write(repo, "tests/test_bad_exclusion.py", source)
        contract.write_text(
            CONTRACT.replace(
                "tests/test_example_contract.py::test_search_integration_exclusion",
                f"tests/test_bad_exclusion.py::{node_name}",
                1,
            ),
            encoding="utf-8",
        )
        result = _run(contract, "--check", "--json")
        assert result.returncode == 1, (variant, result.stderr)
        row = next(
            item
            for item in _payload(result)["results"]
            if item["capability"] == "search.integration"
        )
        assert row["status"] == "MISSING"

    repo, contract = _fixture_repo(tmp_path / "js-comment")
    _write(
        repo,
        "extension/tests/example-exclusions.test.ts",
        'test("example search integration", () => {}); // expect(fake)\n',
    )
    contract.write_text(
        CONTRACT.replace(
            "tests/test_example_contract.py::test_search_integration_exclusion",
            "extension/tests/example-exclusions.test.ts::example search integration",
            1,
        ),
        encoding="utf-8",
    )

    js_comment = _run(contract, "--check", "--json")

    assert js_comment.returncode == 1
    row = next(
        item
        for item in _payload(js_comment)["results"]
        if item["capability"] == "search.integration"
    )
    assert row["status"] == "MISSING"


def test_browser_task_requires_next_result_and_kick_routes_independently(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    _enable_foreground_browser_task(repo, contract, "ex")
    _write(
        repo,
        "extension/tests/ex-task.test.ts",
        'import assert from "node:assert/strict";\n'
        'test("example task", () => { assert.equal(1, 1); });\n',
    )
    app = repo / "src/openbiliclaw/api/app.py"
    app.write_text(
        app.read_text(encoding="utf-8").replace(
            '@app.post("/api/sources/ex/kick")',
            '@not_a_router.missing("/api/sources/ex/kick")',
        ),
        encoding="utf-8",
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    extension = next(
        item for item in _payload(result)["results"] if item["capability"] == "extension.task"
    )
    assert extension["status"] == "MISSING"
    assert "backend POST kick decorator on the same route key" in extension["detail"]


def test_status_does_not_pass_from_an_arbitrary_app_slug(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    app = repo / "src/openbiliclaw/api/app.py"
    app.write_text(
        app.read_text(encoding="utf-8").replace("SOURCE_AUTH_PROVIDERS.items()", "{}.items()")
        + '\nUNRELATED = "example"\n',
        encoding="utf-8",
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    status = next(
        item for item in _payload(result)["results"] if item["capability"] == "api.source-status"
    )
    assert status["status"] == "MISSING"
    assert "dynamic SourcesStatusResponse assembly" in status["detail"]


def test_keyword_platform_words_do_not_fake_inspiration_materialization(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    contract.write_text(
        _without_exclusions(
            CONTRACT.replace('modes = ["feed"]', 'modes = ["search"]'),
            "search.integration",
        ),
        encoding="utf-8",
    )
    _write(
        repo,
        "src/openbiliclaw/runtime/keyword_planner.py",
        '_PLANNER_PLATFORMS = ("example",)\n_PLATFORM_QUERY_STYLES = {"example": "x"}\n',
    )
    _write(
        repo,
        "src/openbiliclaw/llm/prompts.py",
        'PLATFORM_SUPPLY_ADVANTAGES = {"example": "platform"}\nSCHEMA = "platform example"\n',
    )
    _write(
        repo,
        "src/openbiliclaw/runtime/example_producer.py",
        'PLATFORM = "example"\nMODES = ("search",)\ncoordinator.claim("example")\n',
    )
    _write(
        repo,
        "tests/test_example.py",
        "def test_example_source_keyword_id():\n    assert 'source_keyword_id'\n",
    )
    _write(
        repo,
        "tests/test_inspiration_pipeline.py",
        "def test_example_keyword_platform():\n"
        "    assert '_run_inspiration_stage keyword platform ledger inserted'\n",
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    inspiration = next(
        item
        for item in _payload(result)["results"]
        if item["capability"] == "search.inspiration-axis"
    )
    assert inspiration["status"] == "MISSING"


def _enable_incremental_profile(repo: Path, contract: Path) -> None:
    _enable_foreground_browser_task(repo, contract, "ex")
    text = contract.read_text(encoding="utf-8")
    text = text.replace('integration_level = "discovery-only"', 'integration_level = "full"')
    text = text.replace(
        '[profile]\nsignals = false\nincremental = false\nrefresh_mode = "none"',
        '[profile]\nsignals = true\nincremental = true\nrefresh_mode = "incremental"',
    )
    text = text.replace(
        'discover = "extension:browser-task:anonymous"',
        'discover = "extension:browser-task:anonymous"\nprofile = "backend:anonymous"\n'
        'bootstrap = "extension:anonymous"\nincremental = "backend:anonymous"',
    )
    text = text.replace(
        'discover = "anonymous"',
        'discover = "anonymous"\nprofile = "anonymous"\nbootstrap = "anonymous"\n'
        'incremental = "anonymous"',
    )
    text = text.replace(
        "discover = true",
        "discover = true\nprofile = true\nbootstrap = true\nincremental = true",
    )
    text = text.replace('login_state_path = "none"', 'login_state_path = "callback"')
    text = _without_exclusions(
        text,
        "profile.signals",
        "profile.incremental",
        "profile.refresh-mode",
    )
    contract.write_text(text, encoding="utf-8")
    cli = repo / "src/openbiliclaw/cli.py"
    cli.write_text(
        cli.read_text(encoding="utf-8")
        + '\nSOURCE = "example"\nYES_EX = "--yes-ex"\nNO_EX = "--no-ex"\n',
        encoding="utf-8",
    )
    _write(
        repo,
        "src/openbiliclaw/runtime/init_prereqs.py",
        '_PLATFORM_SOURCE_FIELDS = ("example",)\n',
    )
    app = repo / "src/openbiliclaw/api/app.py"
    app.write_text(
        app.read_text(encoding="utf-8") + "\ndef create_app():\n"
        '    _init_write_allowlist = frozenset({"/api/sources/ex/login-state"})\n'
        "    return _init_write_allowlist\n",
        encoding="utf-8",
    )
    _write(
        repo,
        "extension/tests/ex-task.test.ts",
        'import assert from "node:assert/strict";\n'
        'test("example task", () => { assert.equal(1, 1); });\n',
    )
    _write(
        repo,
        "src/openbiliclaw/sources/source_bootstrap.py",
        '_BOOTSTRAP_TASK_TABLES = (("ex", "ex_tasks", "bootstrap_profile"),)\n',
    )
    _write(
        repo,
        "src/openbiliclaw/sources/bootstrap_state.py",
        'SOURCE_BOOTSTRAP_STATE_KEYS = {"ex": "example_seen", "example": "example_seen"}\n'
        'def default_source_bootstrap_state():\n    return {"example_seen": []}\n'
        "def normalize_source_bootstrap_state(state):\n"
        '    return {"example_seen": state.get("example_seen", [])}\n',
    )
    _write(
        repo,
        "src/openbiliclaw/sources/task_result_protocol.py",
        '_TASK_TABLES = frozenset({"ex_tasks"})\n',
    )
    _write(
        repo,
        "tests/test_source_bootstrap.py",
        "def test_ex_bootstrap_registry():\n    assert 'ex_tasks'\n",
    )
    _write(
        repo,
        "src/openbiliclaw/runtime/source_incremental_sync.py",
        "def enqueue_ex():\n    return None\n"
        'SOURCE_ORDER = ("ex",)\n'
        '_TASK_SPECS = {"ex": ("ex_tasks", "bootstrap_profile", enqueue_ex)}\n'
        '_SOURCE_CONFIG_ALIASES = {"ex": ("example", "ex")}\n'
        '_SOURCE_INTERVAL_FIELDS = {"ex": "example_interval"}\n',
    )
    _write(
        repo,
        "tests/test_source_incremental_sync.py",
        "def test_ex_task_specs_registration():\n    assert '_TASK_SPECS' and 'ex_tasks'\n",
    )


def test_profile_browser_task_and_incremental_require_exact_central_registries(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path)
    _enable_incremental_profile(repo, contract)

    complete = _run(contract, "--check", "--json")
    assert complete.returncode == 0, complete.stderr or complete.stdout
    rows = {item["capability"]: item for item in _payload(complete)["results"]}
    assert rows["profile.bootstrap-registries"]["status"] == "PASS"
    assert rows["profile.incremental"]["status"] == "PASS"

    _write(
        repo,
        "src/openbiliclaw/runtime/source_incremental_sync.py",
        "def enqueue_ex():\n    return None\n"
        'SOURCE_ORDER = ("ex",)\n'
        '_TASK_SPECS = {"ex": ("ex_tasks", "bootstrap_profile", enqueue_ex)}\n'
        '_SOURCE_CONFIG_ALIASES = {"ex": ("example", "ex")}\n'
        "_SOURCE_INTERVAL_FIELDS = {}\n",
    )
    missing = _run(contract, "--check", "--json")
    assert missing.returncode == 1
    incremental = next(
        item for item in _payload(missing)["results"] if item["capability"] == "profile.incremental"
    )
    assert incremental["status"] == "MISSING"
    assert "_SOURCE_INTERVAL_FIELDS" in incremental["detail"]


def test_auth_capability_modes_and_required_matrix_are_fail_closed(tmp_path: Path) -> None:
    _repo, contract = _fixture_repo(tmp_path / "enum")
    contract.write_text(
        CONTRACT.replace(
            'discover = "anonymous"\n\n[auth.capability_required]',
            'discover = "browser-ish"\n\n[auth.capability_required]',
        ),
        encoding="utf-8",
    )
    invalid_enum = _run(contract, "--json")
    assert invalid_enum.returncode == 2
    assert "auth.capability_modes.discover must be one of" in invalid_enum.stderr

    _repo, contract = _fixture_repo(tmp_path / "keys")
    contract.write_text(
        CONTRACT.replace(
            "[auth.capability_required]\ndiscover = true",
            "[auth.capability_required]\nother = true",
        ),
        encoding="utf-8",
    )
    mismatched_keys = _run(contract, "--json")
    assert mismatched_keys.returncode == 2
    assert "must declare exactly the same capability keys" in mismatched_keys.stderr

    _repo, contract = _fixture_repo(tmp_path / "optional-login")
    text = CONTRACT.replace('mode = "anonymous"', 'mode = "anonymous-with-optional-credentials"', 1)
    text = text.replace(
        'discover = "backend:anonymous"\n\n[identity]',
        'discover = "backend:anonymous"\nidentity = "extension:login"\n\n[identity]',
    )
    text = text.replace(
        'discover = "anonymous"\n\n[auth.capability_required]',
        'discover = "anonymous"\nidentity = "login-required"\n\n[auth.capability_required]',
    )
    text = text.replace(
        "discover = true\n\n[upstream]", "discover = true\nidentity = true\n\n[upstream]"
    )
    text = text.replace('login_state_path = "none"', 'login_state_path = "callback"')
    text = text.replace('task = "none"', 'task = "identity-only"')
    text = text.replace("hosts = []", 'hosts = ["example.test"]', 1)
    contract.write_text(text, encoding="utf-8")
    invalid_optional = _run(contract, "--json")
    assert invalid_optional.returncode == 2
    assert "no required login-required capability" in invalid_optional.stderr


def test_dead_registry_assignment_cannot_satisfy_required_wiring(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    _write(
        repo,
        "src/openbiliclaw/api/source_auth/providers.py",
        "SOURCE_AUTH_PROVIDERS = {}\n"
        "if False:\n"
        '    SOURCE_AUTH_PROVIDERS = {"example": object()}\n',
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    rows = {item["capability"]: item for item in _payload(result)["results"]}
    assert rows["source-auth.provider"]["status"] == "MISSING"
    assert rows["api.source-status"]["status"] == "MISSING"


def test_browser_heartbeat_requires_readiness_channel_and_source_specific_handler(
    tmp_path: Path,
) -> None:
    _repo, contract = _fixture_repo(tmp_path / "invalid")
    contract.write_text(
        CONTRACT.replace('verify_action = "none"', 'verify_action = "browser_heartbeat"'),
        encoding="utf-8",
    )
    invalid = _run(contract, "--json")
    assert invalid.returncode == 2
    assert "requires extension.cookie_sync=true" in invalid.stderr

    repo, contract = _fixture_repo(tmp_path / "missing-handler")
    text = (
        CONTRACT.replace('verify_action = "none"', 'verify_action = "browser_heartbeat"')
        .replace("login_cookie_names = []", 'login_cookie_names = ["session"]')
        .replace('login_state_path = "none"', 'login_state_path = "callback"')
        .replace("hosts = []", 'hosts = ["example.test"]', 1)
        .replace("cookie_sync = false", "cookie_sync = true")
        .replace(
            'discover = "backend:anonymous"\n\n[identity]',
            'discover = "backend:anonymous"\n'
            'cookie-sync = "extension:callback:login"\n\n[identity]',
        )
        .replace(
            'discover = "anonymous"\n\n[auth.capability_required]',
            'discover = "anonymous"\ncookie-sync = "login-required"\n\n[auth.capability_required]',
        )
        .replace(
            "discover = true\n\n[upstream]",
            "discover = true\ncookie-sync = false\n\n[upstream]",
        )
    )
    text = _without_exclusions(text, "extension.cookie-sync")
    contract.write_text(text, encoding="utf-8")
    _write(
        repo,
        "src/openbiliclaw/api/source_auth/verify.py",
        'VERIFY_ACTIONS = {"example": "browser_heartbeat"}\n_BROWSER_HEARTBEAT_PREFIXES = {}\n',
    )
    _write(
        repo,
        "extension/src/background/cookie-sync.ts",
        'const source = "example"; const host = "example.test";\n',
    )

    missing = _run(contract, "--check", "--json")

    assert missing.returncode == 1
    verify = next(
        item for item in _payload(missing)["results"] if item["capability"] == "source-auth.verify"
    )
    assert verify["status"] == "MISSING"
    assert "browser-heartbeat handler registry" in verify["detail"]

    _write(
        repo,
        "src/openbiliclaw/api/source_auth/verify.py",
        'VERIFY_ACTIONS = {"example": "browser_heartbeat"}\n'
        '_BROWSER_HEARTBEAT_PREFIXES = {"example": "ex"}\n',
    )
    _write(
        repo,
        "src/openbiliclaw/storage/database.py",
        "def get_ex_login_state(): return True, 'current'\n",
    )
    _write(
        repo,
        "extension/src/background/cookie-sync.ts",
        'const event = "ex_login_state_sync_requested";\n',
    )

    no_roundtrip = _run(contract, "--check", "--json")

    assert no_roundtrip.returncode == 1
    verify = next(
        item
        for item in _payload(no_roundtrip)["results"]
        if item["capability"] == "source-auth.verify"
    )
    assert "browser-heartbeat round-trip regression" in verify["detail"]

    _write(
        repo,
        "tests/test_source_auth_contract.py",
        "def test_example_browser_heartbeat_roundtrip():\n"
        "    event = 'ex_login_state_sync_requested'\n"
        "    verification = 'verified'\n"
        "    assert event and verification\n",
    )

    with_roundtrip = _run(contract, "--json")

    verify = next(
        item
        for item in _payload(with_roundtrip)["results"]
        if item["capability"] == "source-auth.verify"
    )
    assert verify["status"] == "PASS"

    repo, contract = _fixture_repo(tmp_path / "foreign-prefix")
    text = (
        CONTRACT.replace('verify_action = "none"', 'verify_action = "browser_heartbeat"')
        .replace("login_cookie_names = []", 'login_cookie_names = ["session"]')
        .replace('login_state_path = "none"', 'login_state_path = "callback"')
        .replace("hosts = []", 'hosts = ["example.test"]', 1)
        .replace("cookie_sync = false", "cookie_sync = true")
        .replace(
            'discover = "backend:anonymous"\n\n[identity]',
            'discover = "backend:anonymous"\n'
            'cookie-sync = "extension:callback:login"\n\n[identity]',
        )
        .replace(
            'discover = "anonymous"\n\n[auth.capability_required]',
            'discover = "anonymous"\ncookie-sync = "login-required"\n\n[auth.capability_required]',
        )
        .replace(
            "discover = true\n\n[upstream]",
            "discover = true\ncookie-sync = false\n\n[upstream]",
        )
    )
    text = _without_exclusions(text, "extension.cookie-sync")
    contract.write_text(text, encoding="utf-8")
    _write(
        repo,
        "src/openbiliclaw/api/source_auth/verify.py",
        'VERIFY_ACTIONS = {"example": "browser_heartbeat"}\n'
        '_BROWSER_HEARTBEAT_PREFIXES = {"example": "zhihu"}\n',
    )
    _write(
        repo,
        "src/openbiliclaw/storage/database.py",
        "def get_zhihu_login_state(): return True, 'wrong source'\n",
    )
    _write(
        repo,
        "extension/src/background/cookie-sync.ts",
        'const event = "zhihu_login_state_sync_requested";\n',
    )

    foreign_prefix = _run(contract, "--check", "--json")

    assert foreign_prefix.returncode == 1
    verify = next(
        item
        for item in _payload(foreign_prefix)["results"]
        if item["capability"] == "source-auth.verify"
    )
    assert verify["status"] == "MISSING"
    assert "browser-heartbeat handler registry" in verify["detail"]


def test_config_write_and_cli_smoke_require_exact_owning_handlers(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path / "config")
    contract.write_text(
        CONTRACT.replace('write_path = "none"', 'write_path = "config-only"'),
        encoding="utf-8",
    )
    app = repo / "src/openbiliclaw/api/app.py"
    app.write_text(
        app.read_text(encoding="utf-8").replace(
            '@app.put("/api/config")\ndef config(): return SourcesConfigOut(example=object())',
            '@app.get("/api/config")\ndef config(): return SourcesConfigOut(example=object())',
        ),
        encoding="utf-8",
    )

    config_only_get = _run(contract, "--check", "--json")

    assert config_only_get.returncode == 1
    write = next(
        item
        for item in _payload(config_only_get)["results"]
        if item["capability"] == "source-auth.write"
    )
    assert write["status"] == "MISSING"
    assert "PUT /api/config" in write["detail"]

    repo, contract = _fixture_repo(tmp_path / "cli")
    _write(repo, "src/openbiliclaw/cli.py", 'INIT_ONLY = "--yes-example"\n')

    init_flag_only = _run(contract, "--check", "--json")

    assert init_flag_only.returncode == 1
    cli = next(
        item
        for item in _payload(init_flag_only)["results"]
        if item["capability"] == "cli.registration"
    )
    assert cli["status"] == "MISSING"
    assert "exact fetch-<source> or discover-<source>" in cli["detail"]


def test_capability_specific_auth_stays_required_missing_until_runtime_support_exists(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path)
    text = (
        CONTRACT.replace('mode = "anonymous"', 'mode = "capability-specific"', 1)
        .replace("login_cookie_names = []", 'login_cookie_names = ["session"]')
        .replace('login_state_path = "none"', 'login_state_path = "callback"')
        .replace("hosts = []", 'hosts = ["example.test"]', 1)
        .replace("cookie_sync = false", "cookie_sync = true")
        .replace(
            'discover = "backend:anonymous"\n\n[identity]',
            'discover = "backend:anonymous"\n'
            'cookie-sync = "extension:callback:login"\n\n[identity]',
        )
        .replace(
            'discover = "anonymous"\n\n[auth.capability_required]',
            'discover = "anonymous"\ncookie-sync = "login-required"\n\n[auth.capability_required]',
        )
        .replace(
            "discover = true\n\n[upstream]",
            "discover = true\ncookie-sync = true\n\n[upstream]",
        )
    )
    text = _without_exclusions(text, "extension.cookie-sync")
    contract.write_text(text, encoding="utf-8")
    _write(
        repo,
        "extension/src/background/cookie-sync.ts",
        'const source = "example"; const host = "example.test";\n',
    )

    result = _run(contract, "--check", "--json")

    assert result.returncode == 1
    row = next(
        item
        for item in _payload(result)["results"]
        if item["capability"] == "source-auth.capability-readiness"
    )
    assert row["required"] is True
    assert row["status"] == "MISSING"


def test_capability_tables_reject_inactive_extras_and_top_level_auth_contradictions(
    tmp_path: Path,
) -> None:
    _repo, contract = _fixture_repo(tmp_path / "inactive")
    text = (
        CONTRACT.replace(
            'discover = "backend:anonymous"\n\n[identity]',
            'discover = "backend:anonymous"\nincremental = "backend:timer"\n\n[identity]',
        )
        .replace(
            'discover = "anonymous"\n\n[auth.capability_required]',
            'discover = "anonymous"\nincremental = "anonymous"\n\n[auth.capability_required]',
        )
        .replace(
            "discover = true\n\n[upstream]",
            "discover = true\nincremental = true\n\n[upstream]",
        )
    )
    contract.write_text(text, encoding="utf-8")
    inactive = _run(contract, "--json")
    assert inactive.returncode == 2
    assert "extra=['incremental']" in inactive.stderr

    _repo, contract = _fixture_repo(tmp_path / "contradiction")
    contract.write_text(
        CONTRACT.replace('discover = "anonymous"', 'discover = "login-required"', 1).replace(
            'login_state_path = "none"', 'login_state_path = "callback"'
        ),
        encoding="utf-8",
    )
    contradiction = _run(contract, "--json")
    assert contradiction.returncode == 2
    assert "auth.mode='anonymous'" in contradiction.stderr


def test_browser_task_endpoints_must_be_real_decorators_on_one_route_key(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path)
    _enable_foreground_browser_task(repo, contract, "ex")
    _write(
        repo,
        "extension/tests/ex-task.test.ts",
        'import assert from "node:assert/strict";\n'
        'test("example task", () => { assert.equal(1, 1); });\n',
    )
    app = repo / "src/openbiliclaw/api/app.py"
    app.write_text(
        app.read_text(encoding="utf-8").replace(
            '"/api/sources/ex/task-result"',
            '"/api/sources/example/task-result"',
        ),
        encoding="utf-8",
    )

    split = _run(contract, "--check", "--json")

    assert split.returncode == 1
    extension = next(
        item for item in _payload(split)["results"] if item["capability"] == "extension.task"
    )
    assert extension["status"] == "MISSING"
    assert "one complete route key" in extension["detail"]


def test_family_alias_host_and_prefix_conflicts_are_rejected(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path)
    platforms = repo / "src/openbiliclaw/sources/platforms.py"
    platforms.write_text(
        platforms.read_text(encoding="utf-8").replace(
            "),)\n",
            "),\n"
            "SourceFamilyRule(\n"
            '    family="other",\n'
            '    platform_aliases=frozenset({"ex"}),\n'
            '    source_prefixes=("example-",),\n'
            '    url_hosts=("example.test",),\n'
            "),)\n",
        ),
        encoding="utf-8",
    )

    result = _run(contract, "--json")

    assert result.returncode == 2
    assert "owners=['example', 'other']" in result.stderr


def test_new_or_partially_registered_family_returns_json_missing_baseline(
    tmp_path: Path,
) -> None:
    repo, contract = _fixture_repo(tmp_path / "new")
    platforms = repo / "src/openbiliclaw/sources/platforms.py"
    platforms.write_text("SOURCE_FAMILY_RULES = ()\n", encoding="utf-8")

    new_source = _run(contract, "--check", "--json")

    assert new_source.returncode == 1
    canonical = next(
        item
        for item in _payload(new_source)["results"]
        if item["capability"] == "canonical.registry"
    )
    assert canonical["status"] == "MISSING"
    assert "PLATFORM_EXAMPLE" in canonical["detail"]

    repo, contract = _fixture_repo(tmp_path / "partial")
    platforms = repo / "src/openbiliclaw/sources/platforms.py"
    platforms.write_text(
        platforms.read_text(encoding="utf-8").replace('"example", "ex"', '"example"'),
        encoding="utf-8",
    )

    partial = _run(contract, "--check", "--json")

    assert partial.returncode == 1
    canonical = next(
        item for item in _payload(partial)["results"] if item["capability"] == "canonical.registry"
    )
    assert canonical["status"] == "MISSING"
    assert "declared alias 'ex'" in canonical["detail"]


def test_n_a_test_must_assert_and_bind_to_the_source(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path / "pass")
    _write(
        repo,
        "tests/test_example_contract.py",
        "def test_example_profile_incremental_fake():\n    pass\n",
    )
    contract.write_text(
        CONTRACT.replace(
            "tests/test_example_contract.py::test_profile_capability_exclusions",
            "tests/test_example_contract.py::test_example_profile_incremental_fake",
            1,
        ),
        encoding="utf-8",
    )
    empty = _run(contract, "--check", "--json")
    assert empty.returncode == 1

    repo, contract = _fixture_repo(tmp_path / "foreign")
    _write(
        repo,
        "tests/test_mobile_card.py",
        "def test_view_models_exposes_stats_formatter():\n    assert 'view'\n",
    )
    contract.write_text(
        CONTRACT.replace(
            "tests/test_example_contract.py::test_engagement_capability_exclusions",
            "tests/test_mobile_card.py::test_view_models_exposes_stats_formatter",
            1,
        ),
        encoding="utf-8",
    )
    unrelated = _run(contract, "--check", "--json")
    assert unrelated.returncode == 1


def test_safe_actions_require_machine_assertions_but_allow_documented_reads(
    tmp_path: Path,
) -> None:
    _repo, contract = _fixture_repo(tmp_path / "mutation")
    contract.write_text(
        CONTRACT.replace("upstream-state-unchanged", "upstream-state-may-change", 1).replace(
            "A bounded public feed GET returns fixture rows and leaves upstream account state "
            "unchanged.",
            "读取公开 feed 后自动关注作者并收藏帖子；上游账号状态会改变。",
        ),
        encoding="utf-8",
    )
    mutation = _run(contract, "--json")
    assert mutation.returncode == 2
    assert "must be exactly 'upstream-state-unchanged'" in mutation.stderr

    _repo, contract = _fixture_repo(tmp_path / "documented")
    text = (
        CONTRACT.replace(
            'safe_actions = ["feed"]',
            'safe_actions = ["feed", "open-public-link", "open-share-panel", "close-share-panel"]',
        )
        .replace(
            '[e2e.safe_assertions]\nfeed = "upstream-state-unchanged"',
            '[e2e.safe_assertions]\nfeed = "upstream-state-unchanged"\n'
            'open-public-link = "upstream-state-unchanged"\n'
            'open-share-panel = "upstream-state-unchanged"\n'
            'close-share-panel = "upstream-state-unchanged"',
        )
        .replace(
            '[e2e.safe_postconditions]\nfeed = "A bounded public feed GET returns fixture rows '
            'and leaves upstream account state unchanged."',
            '[e2e.safe_postconditions]\nfeed = "A bounded public feed GET returns fixture rows '
            'and leaves upstream account state unchanged."\n'
            'open-public-link = "Opens one public URL; no upstream account state changes."\n'
            'open-share-panel = "Opens the share panel; no upstream account state changes."\n'
            'close-share-panel = "Closes the share panel; no upstream account state changes."',
        )
    )
    contract.write_text(text, encoding="utf-8")
    documented = _run(contract, "--json")
    assert documented.returncode == 0, documented.stderr


def test_profile_signal_gate_requires_init_prereqs_and_exact_cli_options(tmp_path: Path) -> None:
    repo, contract = _fixture_repo(tmp_path / "prereqs")
    _enable_incremental_profile(repo, contract)
    (repo / "src/openbiliclaw/runtime/init_prereqs.py").unlink()
    missing_prereqs = _run(contract, "--check", "--json")
    assert missing_prereqs.returncode == 1
    profile = next(
        item
        for item in _payload(missing_prereqs)["results"]
        if item["capability"] == "profile.signals"
    )
    assert "_PLATFORM_SOURCE_FIELDS" in profile["detail"]

    repo, contract = _fixture_repo(tmp_path / "flags")
    _enable_incremental_profile(repo, contract)
    cli = repo / "src/openbiliclaw/cli.py"
    cli.write_text(cli.read_text(encoding="utf-8").replace('YES_EX = "--yes-ex"\n', ""))
    missing_flag = _run(contract, "--check", "--json")
    assert missing_flag.returncode == 1
    profile = next(
        item
        for item in _payload(missing_flag)["results"]
        if item["capability"] == "profile.signals"
    )
    assert "--yes-<source>" in profile["detail"]

    repo, contract = _fixture_repo(tmp_path / "callback")
    _enable_incremental_profile(repo, contract)
    app = repo / "src/openbiliclaw/api/app.py"
    app.write_text(
        app.read_text(encoding="utf-8").replace(
            'frozenset({"/api/sources/ex/login-state"})',
            "frozenset()",
        ),
        encoding="utf-8",
    )
    missing_callback = _run(contract, "--check", "--json")
    assert missing_callback.returncode == 1
    profile = next(
        item
        for item in _payload(missing_callback)["results"]
        if item["capability"] == "profile.signals"
    )
    assert "callback allowlist" in profile["detail"]
