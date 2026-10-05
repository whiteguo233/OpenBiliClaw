from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import generate_tailnet_notices as notices


def test_read_build_tags_requires_exact_audited_contract(tmp_path: Path) -> None:
    tags_file = tmp_path / "build-tags.txt"
    tags_file.write_text("ts_omit_logtail,ts_omit_webclient\n", encoding="utf-8")

    assert notices.read_build_tags(tags_file) == (
        "ts_omit_logtail",
        "ts_omit_webclient",
    )


@pytest.mark.parametrize(
    "value",
    [
        "ts_omit_logtail\n",
        "ts_omit_webclient,ts_omit_logtail\n",
        "ts_omit_logtail,ts_omit_webclient,ts_omit_webclient\n",
        " ts_omit_logtail,ts_omit_webclient\n",
        "ts_omit_logtail,ts-omit-webclient\n",
        "ts_omit_logtail,ts_omit_webclient\nextra\n",
    ],
)
def test_read_build_tags_fails_closed_for_unapproved_content(
    tmp_path: Path,
    value: str,
) -> None:
    tags_file = tmp_path / "build-tags.txt"
    tags_file.write_text(value, encoding="utf-8")

    with pytest.raises(notices.NoticeGenerationError):
        notices.read_build_tags(tags_file)


def test_collect_module_usage_unions_targets_and_skips_main_and_standard(
    tmp_path: Path,
) -> None:
    foo_module = tmp_path / "foo"
    bar_module = tmp_path / "bar"
    package_sets = [
        [
            {"ImportPath": "fmt", "Standard": True},
            {
                "ImportPath": "example.test/helper",
                "Dir": str(tmp_path / "main"),
                "Module": {"Main": True},
            },
            {
                "ImportPath": "example.test/foo/a",
                "Dir": str(foo_module / "a"),
                "Module": {
                    "Path": "example.test/foo",
                    "Version": "v1.2.3",
                    "Dir": str(foo_module),
                },
            },
        ],
        [
            {
                "ImportPath": "example.test/foo/b",
                "Dir": str(foo_module / "b"),
                "Module": {
                    "Path": "example.test/foo",
                    "Version": "v1.2.3",
                    "Dir": str(foo_module),
                },
            },
            {
                "ImportPath": "example.test/bar",
                "Dir": str(bar_module),
                "Module": {
                    "Path": "example.test/bar",
                    "Version": "v2.0.0",
                    "Dir": str(bar_module),
                },
            },
        ],
    ]

    usages = notices.collect_module_usage(package_sets)

    assert [usage.component for usage in usages] == [
        "example.test/bar@v2.0.0",
        "example.test/foo@v1.2.3",
    ]
    assert usages[1].package_dirs == frozenset({foo_module / "a", foo_module / "b"})


def test_prefetch_target_modules_uses_online_listing_for_every_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, tuple[str, ...], bool]] = []
    tags = ("ts_omit_logtail", "ts_omit_webclient")

    monkeypatch.setattr(notices, "read_build_tags", lambda: tags)
    monkeypatch.setattr(notices, "read_required_go_version", lambda: "go1.26.6")
    monkeypatch.setattr(notices, "_go_environment", lambda: ("go1.26.6", Path("/go")))
    monkeypatch.setattr(
        notices,
        "_list_target_packages",
        lambda goos, goarch, build_tags, *, offline=True: calls.append(
            (goos, goarch, build_tags, offline)
        ),
    )

    notices.prefetch_target_modules()

    assert calls == [(*target, tags, False) for target in notices.TARGETS]


def test_collect_module_usage_rejects_replacements(tmp_path: Path) -> None:
    packages = [
        {"ImportPath": "example.test/helper", "Module": {"Main": True}},
        {
            "ImportPath": "example.test/foo",
            "Dir": str(tmp_path / "foo"),
            "Module": {
                "Path": "example.test/foo",
                "Version": "v1.0.0",
                "Dir": str(tmp_path / "foo"),
                "Replace": {"Path": "../foo"},
            },
        },
    ]

    with pytest.raises(notices.NoticeGenerationError, match="replacement modules"):
        notices.collect_module_usage([packages])


def test_collect_legal_files_walks_each_package_to_module_root(tmp_path: Path) -> None:
    module_dir = tmp_path / "module"
    package_a = module_dir / "codec" / "internal" / "a"
    package_b = module_dir / "codec" / "b"
    package_a.mkdir(parents=True)
    package_b.mkdir(parents=True)
    (module_dir / "LICENSE").write_text("root license\n", encoding="utf-8")
    (module_dir / "codec" / "NOTICE.txt").write_text("codec notice\n", encoding="utf-8")
    (package_a / "LICENSE.md").write_text("nested license\n", encoding="utf-8")
    (package_a / "license_test.go").write_text("package a\n", encoding="utf-8")
    (module_dir / "unused").mkdir()
    (module_dir / "unused" / "COPYING").write_text("unused\n", encoding="utf-8")
    usage = notices.ModuleUsage(
        component="example.test/module@v1.0.0",
        module_dir=module_dir,
        package_dirs=frozenset({package_a, package_b}),
    )

    files = notices.collect_module_legal_files(usage)

    assert files == {
        "LICENSE": b"root license\n",
        "codec/NOTICE.txt": b"codec notice\n",
        "codec/internal/a/LICENSE.md": b"nested license\n",
    }


def test_collect_legal_files_fails_when_component_has_no_legal_file(tmp_path: Path) -> None:
    module_dir = tmp_path / "module"
    package_dir = module_dir / "package"
    package_dir.mkdir(parents=True)
    usage = notices.ModuleUsage(
        component="example.test/module@v1.0.0",
        module_dir=module_dir,
        package_dirs=frozenset({package_dir}),
    )

    with pytest.raises(notices.NoticeGenerationError, match="no legal files"):
        notices.collect_module_legal_files(usage)


def test_collect_go_legal_files_requires_exact_version_license_and_patents(
    tmp_path: Path,
) -> None:
    (tmp_path / "LICENSE").write_text("go license\n", encoding="utf-8")
    (tmp_path / "PATENTS").write_text("go patents\n", encoding="utf-8")

    component, files = notices.collect_go_legal_files(tmp_path, "go1.26.6")

    assert component == "go.dev/toolchain@go1.26.6"
    assert files == {"LICENSE": b"go license\n", "PATENTS": b"go patents\n"}

    with pytest.raises(notices.NoticeGenerationError, match="expected go1.26.6"):
        notices.collect_go_legal_files(tmp_path, "go1.26.5")
    (tmp_path / "PATENTS").unlink()
    with pytest.raises(notices.NoticeGenerationError, match="PATENTS"):
        notices.collect_go_legal_files(tmp_path, "go1.26.6")


def test_payloads_deduplicate_exact_bytes_and_render_traceable_markdown() -> None:
    shared = b"shared legal text\n"
    payloads = notices.build_payloads(
        [
            ("example.test/z@v2.0.0", {"NOTICE": shared}),
            ("example.test/a@v1.0.0", {"LICENSE": shared, "PATENTS": b"patent\n"}),
        ]
    )

    rendered = notices.render_notices(
        payloads,
        go_version="go1.26.6",
        tags=("ts_omit_logtail", "ts_omit_webclient"),
    ).decode("utf-8")

    shared_digest = hashlib.sha256(shared).hexdigest()
    assert rendered.count(f"## SHA-256 `{shared_digest}`") == 1
    assert "`example.test/a@v1.0.0` — `LICENSE`" in rendered
    assert "`example.test/z@v2.0.0` — `NOTICE`" in rendered
    assert rendered.count("shared legal text") == 1
    assert "/Users/" not in rendered
    assert "generated at" not in rendered.casefold()


def test_write_or_check_uses_exact_bytes_and_never_repairs_in_check_mode(
    tmp_path: Path,
) -> None:
    output = tmp_path / "notices.md"
    output.write_bytes(b"stale")

    with pytest.raises(notices.NoticeGenerationError, match="stale"):
        notices.write_or_check(output, b"expected", check=True)
    assert output.read_bytes() == b"stale"

    notices.write_or_check(output, b"expected", check=False)
    notices.write_or_check(output, b"expected", check=True)
    assert output.read_bytes() == b"expected"


def test_checked_in_notices_match_offline_generation() -> None:
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({"GOPROXY": "off", "GOWORK": "off"})

    result = subprocess.run(
        [sys.executable, "scripts/generate_tailnet_notices.py", "--check"],
        cwd=project_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
