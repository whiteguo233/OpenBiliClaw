"""Install and locate the signed private runtime without executing untrusted code.

The release catalog is part of Core. Neither a downloaded public key nor a
browser setting can establish trust or choose an executable. Installations keep
versioned directories so failed downloads never overwrite a working runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from openbiliclaw import __version__
from openbiliclaw.proc import no_window_kwargs

from .distribution import is_mac_app_store

PROFILE = "siliconflow-free-v1"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
CATALOG = Path(__file__).with_name("sponsored_release.json")


class BundleError(ValueError):
    """A runtime bundle could not be installed or verified."""


class BundleUnavailableError(BundleError):
    """This Core release has no published bundle for the current platform."""


class BundleExpiredError(BundleError):
    """Authentic installed policy has expired; the application must be updated."""


def platform_id() -> str:
    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    machine = platform.machine().lower()
    arch = {"aarch64": "arm64", "arm64": "arm64", "amd64": "x86_64", "x86_64": "x86_64"}.get(
        machine
    )
    if system is None or arch is None or (system == "windows" and arch != "x86_64"):
        raise BundleError("此平台尚无免费模型运行组件")
    return f"{system}-{arch}"


def _root() -> Path:
    from openbiliclaw.config import _project_root

    return _project_root() / ".sponsored"


def _catalog() -> dict[str, Any]:
    try:
        value = json.loads(CATALOG.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("unknown catalog")
        return value
    except (OSError, ValueError) as exc:
        raise BundleError("免费模型发行目录不可用，请更新 OpenBiliClaw") from exc


def _version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise BundleError("invalid Core compatibility version")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _store_bundle_directory() -> Path | None:
    if not is_mac_app_store():
        return None
    return Path(sys.executable).resolve().parent.parent / "Resources/sponsored"


def verify_bundle(
    directory: Path,
    *,
    catalog: dict[str, Any] | None = None,
    expected_platform: str | None = None,
) -> dict[str, Any]:
    """Verify signatures, platform, compatibility and every byte before execution."""
    from openbiliclaw.llm.sponsored_profiles import approved_registry

    catalog = _catalog() if catalog is None else catalog
    target_platform = expected_platform or platform_id()
    try:
        public_bytes = bytes.fromhex(str(catalog["public_key_hex"]))
        public = Ed25519PublicKey.from_public_bytes(public_bytes)
        raw = (directory / "manifest.json").read_bytes()
        if len(raw) > 65536:
            raise BundleError("manifest too large")
        public.verify((directory / "manifest.sig").read_bytes(), raw)
        manifest = json.loads(raw)
        if not isinstance(manifest, dict):
            raise BundleError("invalid manifest")
        executable = (
            "obc-sponsored-runtime.exe"
            if target_platform.startswith("windows-")
            else "obc-sponsored-runtime"
        )
        names = {
            executable,
            "sponsored-policy.json",
            "sponsored-policy.sig",
            "release-public-key.bin",
        }
        members = names | {"manifest.json", "manifest.sig"}
        if directory == _store_bundle_directory():
            # The native host verifies the signed app and its nested code at launch.
            # Apple distribution re-signs the helper, so its original SHA cannot
            # authenticate those signature bytes. Keep the policy's Ed25519 checks.
            binary = directory.parent.parent / "MacOS" / executable
            if directory.is_symlink() or binary.is_symlink() or not binary.is_file():
                raise BundleError("应用内的免费模型组件缺失，请从 App Store 更新")
            members.remove(executable)
        if (
            manifest.get("protocol") != 2
            or manifest.get("profile") != PROFILE
            or manifest.get("platform") != target_platform
            or not _version(manifest.get("core_min"))
            <= _version(__version__)
            <= _version(manifest.get("core_max"))
            or set(manifest.get("files", {})) != names
            or {p.name for p in directory.iterdir()} != members
        ):
            raise BundleError("免费模型组件与当前版本或平台不兼容")
        for name in members:
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_UNPACKED_BYTES:
                raise BundleError("invalid bundle member")
            if (
                name in names
                and hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"][name]
            ):
                raise BundleError("免费模型组件校验失败")
        if (directory / "release-public-key.bin").read_bytes() != public_bytes:
            raise BundleError("bundle trust key mismatch")
        policy_bytes = (directory / "sponsored-policy.json").read_bytes()
        public.verify((directory / "sponsored-policy.sig").read_bytes(), policy_bytes)
        policy = json.loads(policy_bytes)
        expiry = datetime.fromisoformat(policy["expires_at"].replace("Z", "+00:00"))
        if expiry <= datetime.now(UTC):
            raise BundleExpiredError("免费模型组件已到期，请更新 OpenBiliClaw 安装包/版本")
        if policy["policy_version"] != manifest["policy_version"]:
            raise BundleError("免费模型组件策略版本不匹配，请更新")
        expected = {f"{c.task_id}.v{c.contract_version}": c for c in approved_registry()}
        if set(policy["tasks"]) != set(expected):
            raise BundleError("免费模型组件缺少所需任务")
        for name, contract in expected.items():
            task = policy["tasks"][name]
            expected_task = contract.to_policy_task()
            if any(
                task.get(key) != value for key, value in expected_task.items() if key != "limits"
            ):
                raise BundleError("免费模型组件任务契约不匹配")
        return manifest
    except BundleError:
        raise
    except (OSError, ValueError, KeyError, TypeError, InvalidSignature) as exc:
        raise BundleError("免费模型组件签名或内容校验失败") from exc


def _command(directory: Path) -> str:
    verify_bundle(directory)
    executable = "obc-sponsored-runtime.exe" if os.name == "nt" else "obc-sponsored-runtime"
    binary_dir = (
        directory.parent.parent / "MacOS" if directory == _store_bundle_directory() else directory
    )
    return shlex.join(
        [
            str(binary_dir / executable),
            "--policy",
            str(directory / "sponsored-policy.json"),
            "--signature",
            str(directory / "sponsored-policy.sig"),
            "--data-dir",
            str(_root() / "ledger"),
        ]
    )


def _installed_candidates() -> list[Path]:
    store = _store_bundle_directory()
    if store is not None:
        return [store]
    bundled = os.environ.get("OPENBILICLAW_SPONSORED_BUNDLE", "").strip()
    try:
        active = (_root() / "active").read_text().strip()
    except OSError:
        active = ""
    candidates = []
    if re.fullmatch(r"[0-9a-f]{64}", active):
        candidates.append(_root() / "bundles" / active)
    if bundled:
        candidates.append(Path(bundled))
    # An application upgrade can carry a new credential generation while the
    # user's active pointer still names an older, validly signed release. Prefer
    # the Core-pinned manifest already on disk before considering that fallback;
    # replacing an app must work offline and must not keep using its retired Key.
    try:
        pinned = _catalog().get("releases", {}).get(platform_id(), {}).get("manifest_sha256")
    except BundleError:
        pinned = None
    if isinstance(pinned, str) and pinned:
        expected_digest = pinned

        def matches_pin(path: Path) -> bool:
            try:
                return (
                    hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()
                    == expected_digest
                )
            except OSError:
                return False

        candidates.sort(key=lambda path: not matches_pin(path))
    return candidates


def installed_runtime_command() -> str:
    """Return only a verified bundle command; explicit local CLI overrides live elsewhere."""
    for path in _installed_candidates():
        try:
            return _command(path)
        except BundleError:
            continue
    return ""


def bundle_status() -> dict[str, Any]:
    """Safe UI metadata, without local executable paths or credentials."""
    expired = False
    for path in _installed_candidates():
        try:
            _command(path)
            return {"available": True, "code": "", "message": "免费模型组件已就绪"}
        except BundleExpiredError:
            expired = True
        except BundleError:
            continue
    return {
        "available": False,
        "code": "POLICY_INVALID" if expired else "",
        "message": "免费模型组件已到期，请更新安装包/版本"
        if expired
        else "免费模型组件尚未安装或需要更新",
    }


def install_archive(archive: Path, *, catalog: dict[str, Any] | None = None) -> Path:
    """Safely unpack, verify and atomically activate an archive (also used by packagers)."""
    root = _root()
    bundles = root / "bundles"
    bundles.mkdir(parents=True, exist_ok=True, mode=0o700)
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BundleError("runtime archive too large")
    with tempfile.TemporaryDirectory(prefix="install-", dir=root) as scratch:
        temporary = Path(scratch) / "bundle"
        temporary.mkdir()
        total = 0
        allowed = {
            "obc-sponsored-runtime",
            "obc-sponsored-runtime.exe",
            "sponsored-policy.json",
            "sponsored-policy.sig",
            "release-public-key.bin",
            "manifest.json",
            "manifest.sig",
        }
        with tarfile.open(archive, "r:gz") as tar:
            seen: set[str] = set()
            for member in tar:
                total += member.size
                if (
                    member.name not in allowed
                    or member.name in seen
                    or not member.isfile()
                    or total > MAX_UNPACKED_BYTES
                ):
                    raise BundleError("unsafe runtime archive")
                seen.add(member.name)
                source = tar.extractfile(member)
                if source is None:
                    raise BundleError("missing archive member")
                destination = temporary / member.name
                with source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
                destination.chmod(
                    0o700 if member.name.startswith("obc-sponsored-runtime") else 0o600
                )
        verify_bundle(temporary, catalog=catalog)
        binary = temporary / (
            "obc-sponsored-runtime.exe" if os.name == "nt" else "obc-sponsored-runtime"
        )
        try:
            check = subprocess.run(
                [
                    str(binary),
                    "--policy",
                    str(temporary / "sponsored-policy.json"),
                    "--signature",
                    str(temporary / "sponsored-policy.sig"),
                    "--check-policy",
                ],
                capture_output=True,
                timeout=15,
                check=False,
                **no_window_kwargs(),
            )
            if check.returncode:
                raise BundleError("免费模型组件离线自检失败，已保留原组件")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BundleError("免费模型组件无法运行，已保留原组件") from exc
        identity = hashlib.sha256((temporary / "manifest.json").read_bytes()).hexdigest()
        destination = bundles / identity
        if destination.exists():
            verify_bundle(destination, catalog=catalog)
        else:
            try:
                temporary.rename(destination)
            except FileExistsError:
                verify_bundle(destination, catalog=catalog)
        pointer = Path(scratch) / "active"
        pointer.write_text(identity, encoding="ascii")
        os.replace(pointer, root / "active")
    return destination


def ensure_runtime() -> str:
    """Acquire the Core-pinned release. Never download a URL supplied by a browser."""
    current = installed_runtime_command()
    if is_mac_app_store():
        if current:
            return current
        raise BundleUnavailableError("请从 App Store 更新免费模型组件")

    import httpx

    from openbiliclaw.config import load_config
    from openbiliclaw.network import httpx_kwargs_for

    catalog = _catalog()
    release = catalog.get("releases", {}).get(platform_id())
    if not release:
        if current:
            return current
        raise BundleUnavailableError(
            "此版本尚未配置已签名的免费模型安装包，请安装包含该组件的正式版本"
        )
    if current and release.get("manifest_sha256"):
        installed = Path(shlex.split(current)[0]).parent / "manifest.json"
        if hashlib.sha256(installed.read_bytes()).hexdigest() == release["manifest_sha256"]:
            return current
    root = _root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected = str(release.get("sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise BundleError("invalid pinned archive digest")
    url = str(release.get("url", ""))
    # Bootstrap and packaging call this before API/CLI network initialization.
    # Resolve the saved policy here instead of inheriting the pre-init direct sentinel.
    network = load_config().network
    with tempfile.TemporaryDirectory(prefix="download-", dir=root) as scratch:
        archive = Path(scratch) / "bundle.tar.gz"
        with httpx.Client(
            timeout=60,
            follow_redirects=False,
            **httpx_kwargs_for(network.mode, network.proxy),
        ) as client:
            for _ in range(6):
                if urlparse(url).scheme != "https" or urlparse(url).username is not None:
                    raise BundleError("runtime downloads require HTTPS")
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    size = 0
                    digest = hashlib.sha256()
                    with archive.open("wb") as target:
                        for part in response.iter_bytes():
                            size += len(part)
                            if size > MAX_ARCHIVE_BYTES:
                                raise BundleError("runtime download too large")
                            target.write(part)
                            digest.update(part)
                    if digest.hexdigest() != expected:
                        raise BundleError("runtime download digest mismatch")
                    install_archive(archive)
                    return installed_runtime_command()
            raise BundleError("too many runtime download redirects")


def stage_runtime_bundle(destination: Path) -> bool:
    """Require and stage the exact Core-pinned release; never ship an incomplete package."""
    from .relay_release import official_relay_url

    if official_relay_url():
        if destination.exists():
            raise BundleError("relay-only package must not contain an old key-bearing SDK")
        return True
    catalog = _catalog()
    release = catalog.get("releases", {}).get(platform_id())
    if not release:
        raise BundleUnavailableError("此平台缺少正式免费模型组件，已停止打包")
    pinned = release.get("manifest_sha256", "")
    if not isinstance(pinned, str) or not re.fullmatch(r"[0-9a-f]{64}", pinned):
        raise BundleError("missing pinned runtime manifest digest")
    command = ensure_runtime()
    source = Path(shlex.split(command)[0]).parent
    verify_bundle(source)
    if hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest() != pinned:
        raise BundleError("runtime does not match the Core-pinned manifest")
    if destination.exists():
        raise BundleError("refusing to overwrite a staged runtime bundle")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sponsored-stage-", dir=destination.parent) as scratch:
        staged = Path(scratch) / "bundle"
        shutil.copytree(source, staged)
        verify_bundle(staged)
        if hashlib.sha256((staged / "manifest.json").read_bytes()).hexdigest() != pinned:
            raise BundleError("staged runtime does not match the Core-pinned manifest")
        staged.rename(destination)
    return True


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="install an independently signed local bundle")
    parser.add_argument(
        "--stage", type=Path, help="stage the verified release for a native package"
    )
    parser.add_argument(
        "--if-available", action="store_true", help="allow releases without a bundle catalog"
    )
    args = parser.parse_args()
    from .relay_release import official_relay_url

    if official_relay_url() and not args.archive and not args.stage:
        print("免费服务使用 HTTPS 中继，无需下载含供应商凭据的本地组件")
        return
    try:
        if args.stage:
            if not stage_runtime_bundle(args.stage):
                if not args.if_available:
                    raise BundleError("此版本尚未配置正式免费模型组件")
                print("尚无免费模型发行包，保留初始化配置入口")
                return
        elif args.archive:
            install_archive(args.archive)
        else:
            ensure_runtime()
        print("免费模型组件已安装并通过签名校验")
    except BundleUnavailableError as exc:
        if args.if_available:
            print(str(exc))
            return
        parser.exit(1, f"{exc}\n")
    except BundleError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
