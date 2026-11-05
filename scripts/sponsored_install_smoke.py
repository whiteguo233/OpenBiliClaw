#!/usr/bin/env python3
"""Exercise the real pinned SDK download and native installation without model calls."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import tarfile
import tempfile
from pathlib import Path


def main() -> None:
    from openbiliclaw.runtime import sponsored_bundle as sdk

    with tempfile.TemporaryDirectory(prefix="obc-sdk-install-") as scratch:
        root = Path(scratch)
        previous = os.environ.get("OPENBILICLAW_PROJECT_ROOT")
        os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root / "project")
        try:
            command = sdk.ensure_runtime()
            assert command, "download did not activate a runtime"
            installed = Path(shlex.split(command)[0]).parent
            manifest = sdk.verify_bundle(installed)
            assert sdk.ensure_runtime() == command, "second install changed the active runtime"
            stage = root / "desktop-resources"
            assert sdk.stage_runtime_bundle(stage), "desktop staging did not include the runtime"
            assert sdk.verify_bundle(stage) == manifest

            # Simulate a damaged update. It must fail before execution and preserve the active SDK.
            damaged = root / "damaged"
            shutil.copytree(stage, damaged)
            binary = damaged / Path(shlex.split(command)[0]).name
            binary.write_bytes(binary.read_bytes() + b"tampered")
            archive = root / "damaged.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                for path in sorted(damaged.iterdir()):
                    stream.add(path, arcname=path.name, recursive=False)
            try:
                sdk.install_archive(archive)
            except sdk.BundleError:
                pass
            else:
                raise AssertionError("damaged update was accepted")
            assert sdk.installed_runtime_command() == command, (
                "failed update displaced the working SDK"
            )
            print(
                json.dumps(
                    {
                        "platform": sdk.platform_id(),
                        "manifest_sha256": hashlib.sha256(
                            (installed / "manifest.json").read_bytes()
                        ).hexdigest(),
                        "download_native_check_activation": True,
                        "repeat_install_reuses_active": True,
                        "desktop_stage_verified": True,
                        "damaged_update_preserves_active": True,
                        "model_api_calls": 0,
                    },
                    indent=2,
                )
            )
        finally:
            if previous is None:
                os.environ.pop("OPENBILICLAW_PROJECT_ROOT", None)
            else:
                os.environ["OPENBILICLAW_PROJECT_ROOT"] = previous


if __name__ == "__main__":
    main()
