#!/usr/bin/env python3
"""Generate deterministic third-party notices for the embedded Tailnet helper."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER_DIR = PROJECT_ROOT / "cmd" / "openbiliclaw-tailnet"
BUILD_TAGS_FILE = HELPER_DIR / "build-tags.txt"
DEFAULT_OUTPUT = PROJECT_ROOT / "THIRD_PARTY_NOTICES.md"

EXPECTED_GO_VERSION = "go1.26.6"
EXPECTED_BUILD_TAGS = ("ts_omit_logtail", "ts_omit_webclient")
TARGETS = (
    ("darwin", "amd64"),
    ("darwin", "arm64"),
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("windows", "amd64"),
)

_LEGAL_FILE_RE = re.compile(
    r"^(?:LICENSE|LICENCE|COPYING|NOTICE|PATENTS)(?:$|[.-])",
    re.IGNORECASE,
)
_BUILD_TAG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_GO_DIRECTIVE_RE = re.compile(r"^go\s+(\S+)(?:\s*//.*)?$")


class NoticeGenerationError(RuntimeError):
    """Raised when notice generation cannot prove a complete legal inventory."""


@dataclass(frozen=True, order=True)
class LegalReference:
    """A trace from a legal text to its component and module-relative file."""

    component: str
    relative_path: str


@dataclass(frozen=True)
class ModuleUsage:
    """The package directories used from one external Go module."""

    component: str
    module_dir: Path
    package_dirs: frozenset[Path]


@dataclass(frozen=True)
class LegalPayload:
    """One exact legal-file payload and all references to it."""

    digest: str
    content: bytes
    references: tuple[LegalReference, ...]


def read_build_tags(path: Path = BUILD_TAGS_FILE) -> tuple[str, ...]:
    """Read and strictly validate the helper's audited build-tag contract."""

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise NoticeGenerationError(f"cannot read build tags: {path}: {exc}") from exc

    lines = raw.splitlines()
    if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
        raise NoticeGenerationError("build-tags.txt must contain one non-empty, unpadded line")

    tags = tuple(lines[0].split(","))
    if any(not tag or not _BUILD_TAG_RE.fullmatch(tag) for tag in tags):
        raise NoticeGenerationError("build-tags.txt contains an invalid Go build tag")
    if len(set(tags)) != len(tags):
        raise NoticeGenerationError("build-tags.txt contains duplicate Go build tags")
    if tags != EXPECTED_BUILD_TAGS:
        expected = ",".join(EXPECTED_BUILD_TAGS)
        raise NoticeGenerationError(
            f"build-tags.txt must exactly match the audited tag set: {expected}"
        )
    return tags


def read_required_go_version(go_mod_path: Path = HELPER_DIR / "go.mod") -> str:
    """Return the helper's Go directive, requiring the audited toolchain version."""

    try:
        lines = go_mod_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise NoticeGenerationError(f"cannot read {go_mod_path}: {exc}") from exc

    versions = [match.group(1) for line in lines if (match := _GO_DIRECTIVE_RE.match(line))]
    if len(versions) != 1:
        raise NoticeGenerationError("helper go.mod must contain exactly one Go directive")
    version = f"go{versions[0]}"
    if version != EXPECTED_GO_VERSION:
        raise NoticeGenerationError(
            f"helper go.mod requires {version}; expected {EXPECTED_GO_VERSION}"
        )
    return version


def _go_environment() -> tuple[str, Path]:
    env = _base_go_environment()
    command = ["go", "env", "-json", "GOVERSION", "GOROOT"]
    result = _run_command(command, cwd=HELPER_DIR, env=env)
    try:
        values = json.loads(result.stdout)
        version = values["GOVERSION"]
        goroot = Path(values["GOROOT"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise NoticeGenerationError("go env returned invalid GOVERSION/GOROOT JSON") from exc
    if not isinstance(version, str) or not isinstance(values["GOROOT"], str):
        raise NoticeGenerationError("go env returned non-string GOVERSION/GOROOT values")
    if version != EXPECTED_GO_VERSION:
        raise NoticeGenerationError(
            f"active Go toolchain is {version}; expected {EXPECTED_GO_VERSION}"
        )
    if not goroot.is_dir():
        raise NoticeGenerationError(f"active Go GOROOT does not exist: {goroot}")
    return version, goroot


def _base_go_environment(*, offline: bool = True) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "CGO_ENABLED": "0",
            "GOFLAGS": "",
            "GOWORK": "off",
        }
    )
    if offline:
        env["GOPROXY"] = "off"
    return env


def _run_command(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise NoticeGenerationError(f"cannot execute {command[0]}: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or "no stderr"
        raise NoticeGenerationError(f"{' '.join(command)} failed: {stderr}")
    return result


def _decode_json_stream(raw: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    position = 0
    objects: list[dict[str, Any]] = []
    while position < len(raw):
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position == len(raw):
            break
        try:
            value, position = decoder.raw_decode(raw, position)
        except json.JSONDecodeError as exc:
            raise NoticeGenerationError(f"go list returned invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise NoticeGenerationError("go list returned a non-object JSON value")
        objects.append(value)
    if not objects:
        raise NoticeGenerationError("go list returned no packages")
    return objects


def _list_target_packages(
    goos: str,
    goarch: str,
    tags: tuple[str, ...],
    *,
    offline: bool = True,
) -> list[dict[str, Any]]:
    env = _base_go_environment(offline=offline)
    env.update({"GOOS": goos, "GOARCH": goarch})
    command = [
        "go",
        "list",
        "-mod=readonly",
        f"-tags={','.join(tags)}",
        "-deps",
        "-json",
        ".",
    ]
    result = _run_command(command, cwd=HELPER_DIR, env=env)
    packages = _decode_json_stream(result.stdout)
    for package in packages:
        if package.get("Error") or package.get("DepsErrors"):
            import_path = package.get("ImportPath", "<unknown>")
            raise NoticeGenerationError(f"go list reported dependency errors for {import_path}")
    return packages


def collect_module_usage(package_sets: list[list[dict[str, Any]]]) -> tuple[ModuleUsage, ...]:
    """Union external module packages across targets and reject ambiguous metadata."""

    packages_by_component: dict[str, set[Path]] = defaultdict(set)
    directories_by_component: dict[str, Path] = {}
    saw_main_module = False

    for packages in package_sets:
        for package in packages:
            if package.get("Standard"):
                continue
            module = package.get("Module")
            if module is None:
                import_path = package.get("ImportPath", "<unknown>")
                raise NoticeGenerationError(
                    f"non-standard package has no module metadata: {import_path}"
                )
            if not isinstance(module, dict):
                raise NoticeGenerationError("go list returned invalid module metadata")
            if module.get("Main"):
                saw_main_module = True
                continue
            if module.get("Replace") is not None:
                path = module.get("Path", "<unknown>")
                raise NoticeGenerationError(
                    f"replacement modules require explicit license review: {path}"
                )

            module_path = module.get("Path")
            version = module.get("Version")
            module_dir_raw = module.get("Dir")
            package_dir_raw = package.get("Dir")
            if (
                not isinstance(module_path, str)
                or not module_path
                or not isinstance(version, str)
                or not version
                or not isinstance(module_dir_raw, str)
                or not module_dir_raw
                or not isinstance(package_dir_raw, str)
                or not package_dir_raw
            ):
                import_path = package.get("ImportPath", "<unknown>")
                raise NoticeGenerationError(
                    f"incomplete module metadata for package: {import_path}"
                )

            component = f"{module_path}@{version}"
            module_dir = Path(module_dir_raw)
            package_dir = Path(package_dir_raw)
            previous_dir = directories_by_component.setdefault(component, module_dir)
            if previous_dir != module_dir:
                raise NoticeGenerationError(f"module resolved to multiple directories: {component}")
            packages_by_component[component].add(package_dir)

    if not saw_main_module:
        raise NoticeGenerationError("go list did not identify the helper's main module")
    if not packages_by_component:
        raise NoticeGenerationError("go list found no external helper dependencies")

    return tuple(
        ModuleUsage(
            component=component,
            module_dir=directories_by_component[component],
            package_dirs=frozenset(package_dirs),
        )
        for component, package_dirs in sorted(packages_by_component.items())
    )


def _is_legal_filename(name: str) -> bool:
    if _LEGAL_FILE_RE.match(name) is None:
        return False
    return not name.casefold().endswith(
        (".go", ".json", ".pebble", ".sh", ".toml", ".tmpl", ".yaml", ".yml")
    )


def collect_module_legal_files(usage: ModuleUsage) -> dict[str, bytes]:
    """Collect legal files on every used package-to-module-root path."""

    module_dir = usage.module_dir.resolve()
    if not module_dir.is_dir():
        raise NoticeGenerationError(f"module directory is missing for {usage.component}")

    legal_paths: set[Path] = set()
    for package_dir_raw in sorted(usage.package_dirs):
        package_dir = package_dir_raw.resolve()
        if not package_dir.is_dir():
            raise NoticeGenerationError(
                f"package directory is missing for {usage.component}: {package_dir_raw}"
            )
        try:
            package_dir.relative_to(module_dir)
        except ValueError as exc:
            raise NoticeGenerationError(
                f"package directory escapes module root for {usage.component}"
            ) from exc

        current = package_dir
        while True:
            try:
                entries = sorted(
                    current.iterdir(), key=lambda path: (path.name.casefold(), path.name)
                )
            except OSError as exc:
                raise NoticeGenerationError(
                    f"cannot inspect legal files for {usage.component}"
                ) from exc
            for path in entries:
                if path.is_file() and _is_legal_filename(path.name):
                    legal_paths.add(path)
            if current == module_dir:
                break
            current = current.parent

    if not legal_paths:
        raise NoticeGenerationError(f"no legal files found for dependency: {usage.component}")

    files: dict[str, bytes] = {}
    for path in sorted(legal_paths, key=lambda item: item.relative_to(module_dir).as_posix()):
        relative_path = path.relative_to(module_dir).as_posix()
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise NoticeGenerationError(
                f"cannot read legal file for {usage.component}: {relative_path}"
            ) from exc
        if not content.strip():
            raise NoticeGenerationError(
                f"legal file is empty for {usage.component}: {relative_path}"
            )
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NoticeGenerationError(
                f"legal file is not UTF-8 for {usage.component}: {relative_path}"
            ) from exc
        files[relative_path] = content
    return files


def collect_go_legal_files(goroot: Path, go_version: str) -> tuple[str, dict[str, bytes]]:
    """Collect the required Go distribution LICENSE and PATENTS payloads."""

    if go_version != EXPECTED_GO_VERSION:
        raise NoticeGenerationError(
            f"cannot inventory Go {go_version}; expected {EXPECTED_GO_VERSION}"
        )
    files: dict[str, bytes] = {}
    for name in ("LICENSE", "PATENTS"):
        path = goroot / name
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise NoticeGenerationError(f"required Go legal file is missing: {name}") from exc
        if not content.strip():
            raise NoticeGenerationError(f"required Go legal file is empty: {name}")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NoticeGenerationError(f"required Go legal file is not UTF-8: {name}") from exc
        files[name] = content
    return f"go.dev/toolchain@{go_version}", files


def build_payloads(
    module_files: list[tuple[str, dict[str, bytes]]],
) -> tuple[LegalPayload, ...]:
    """Deduplicate legal files by exact SHA-256 while preserving every reference."""

    contents: dict[str, bytes] = {}
    references: dict[str, set[LegalReference]] = defaultdict(set)
    for component, files in module_files:
        if "@" not in component:
            raise NoticeGenerationError(f"component is not path@version: {component}")
        for relative_path, content in files.items():
            if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
                raise NoticeGenerationError(
                    f"legal reference is not module-relative: {relative_path}"
                )
            digest = hashlib.sha256(content).hexdigest()
            previous = contents.setdefault(digest, content)
            if previous != content:
                raise NoticeGenerationError(f"SHA-256 collision while processing {component}")
            references[digest].add(LegalReference(component, relative_path))

    if not contents:
        raise NoticeGenerationError("no legal payloads were collected")
    payloads = [
        LegalPayload(
            digest=digest,
            content=content,
            references=tuple(sorted(references[digest])),
        )
        for digest, content in contents.items()
    ]
    return tuple(sorted(payloads, key=lambda payload: (payload.references, payload.digest)))


def _markdown_fence(content: str) -> str:
    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
    return "`" * max(3, longest_run + 1)


def render_notices(
    payloads: tuple[LegalPayload, ...],
    *,
    go_version: str,
    tags: tuple[str, ...],
) -> bytes:
    """Render a deterministic Markdown notice document."""

    lines = [
        "# Third-Party Notices for the Embedded Tailnet Helper\n",
        "\n",
        "This file is generated by `python scripts/generate_tailnet_notices.py`. ",
        "Do not edit it by hand.\n",
        "\n",
        "It inventories the union of packages compiled for the supported desktop helper ",
        "targets, with CGO disabled. The OpenBiliClaw main-module license is bundled ",
        "separately and is intentionally excluded here.\n",
        "\n",
        f"- Go toolchain: `{go_version}`\n",
        f"- Build tags: `{','.join(tags)}`\n",
        "- Targets: " + ", ".join(f"`{goos}/{goarch}`" for goos, goarch in TARGETS) + "\n",
        "\n",
        "Legal-file payloads below are deduplicated by the exact SHA-256 of their bytes. ",
        "Each reference names the Go component (`path@version`) and its module-relative ",
        "legal-file path.\n",
        "\n",
    ]

    for payload in payloads:
        lines.append(f"## SHA-256 `{payload.digest}`\n")
        lines.append("\n")
        lines.append("References:\n")
        lines.append("\n")
        for reference in payload.references:
            lines.append(f"- `{reference.component}` — `{reference.relative_path}`\n")
        lines.append("\n")
        content = payload.content.decode("utf-8")
        fence = _markdown_fence(content)
        lines.append(f"{fence}text\n")
        lines.append(content)
        if not content.endswith("\n"):
            lines.append("\n")
        lines.append(f"{fence}\n")
        lines.append("\n")

    return ("".join(lines).rstrip("\n") + "\n").encode("utf-8")


def generate_notice_bytes() -> bytes:
    """Generate the complete deterministic Tailnet helper notice document."""

    tags = read_build_tags()
    required_go_version = read_required_go_version()
    active_go_version, goroot = _go_environment()
    if active_go_version != required_go_version:
        raise NoticeGenerationError(
            f"active Go toolchain {active_go_version} does not match go.mod {required_go_version}"
        )

    package_sets = [_list_target_packages(goos, goarch, tags) for goos, goarch in TARGETS]
    usages = collect_module_usage(package_sets)
    module_files = [(usage.component, collect_module_legal_files(usage)) for usage in usages]
    module_files.append(collect_go_legal_files(goroot, active_go_version))
    payloads = build_payloads(module_files)
    return render_notices(payloads, go_version=active_go_version, tags=tags)


def prefetch_target_modules() -> None:
    """Populate the Go module cache for every audited helper target."""

    tags = read_build_tags()
    required_go_version = read_required_go_version()
    active_go_version, _ = _go_environment()
    if active_go_version != required_go_version:
        raise NoticeGenerationError(
            f"active Go toolchain {active_go_version} does not match go.mod {required_go_version}"
        )
    for goos, goarch in TARGETS:
        _list_target_packages(goos, goarch, tags, offline=False)


def write_or_check(output: Path, expected: bytes, *, check: bool) -> None:
    """Atomically write output, or fail unless it already matches byte-for-byte."""

    if check:
        try:
            actual = output.read_bytes()
        except OSError as exc:
            raise NoticeGenerationError(f"cannot read notice output for --check: {output}") from exc
        # Git for Windows may check the committed file out with CRLF; compare
        # logically rather than byte-for-byte so generated LF notices still pass
        # on Windows runners.
        def _normalize_newlines(data: bytes) -> bytes:
            return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

        if _normalize_newlines(actual) != _normalize_newlines(expected):
            raise NoticeGenerationError(
                f"notice output is stale: {output}; run scripts/generate_tailnet_notices.py"
            )
        return

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=f".{output.name}.", delete=False
        ) as tmp:
            temporary_path = Path(tmp.name)
            tmp.write(expected)
        temporary_path.replace(output)
    except OSError as exc:
        raise NoticeGenerationError(f"cannot write notice output: {output}: {exc}") from exc
    finally:
        if "temporary_path" in locals():
            temporary_path.unlink(missing_ok=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="notice Markdown path (default: repository THIRD_PARTY_NOTICES.md)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="fail unless the output matches freshly generated bytes",
    )
    mode.add_argument(
        "--prefetch",
        action="store_true",
        help="download the exact modules needed by every audited target, then exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.prefetch:
            prefetch_target_modules()
            print(f"prefetched modules for {len(TARGETS)} target(s)")
            return 0
        expected = generate_notice_bytes()
        write_or_check(args.output, expected, check=args.check)
    except NoticeGenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.check:
        print(f"verified {args.output}")
    else:
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
