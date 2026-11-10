"""The frozen runtime must keep Ego's relative native module imports intact."""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest


def _project_data_files() -> dict[str, Path]:
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "packaging" / "openbiliclaw.spec").read_text())
    analysis = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    )
    data = next(keyword.value for keyword in analysis.keywords if keyword.arg == "datas")
    while isinstance(data, ast.BinOp):
        data = data.left
    assert isinstance(data, ast.List)
    # Read only the spec's project-resource list, without importing PyInstaller
    # or executing its build/signing/dependency-collection setup.
    entries = eval(
        compile(ast.Expression(data), "<spec project resources>", "eval"),
        {"__builtins__": {}, "project_root": root, "str": str},
    )
    files = {}
    for source, target in entries:
        path = Path(source)
        if path.is_dir():
            for member in path.rglob("*"):
                if member.is_file():
                    files[(Path(target) / member.relative_to(path)).as_posix()] = member
        else:
            files[(Path(target) / path.name).as_posix()] = path
    return files


def test_frozen_resources_include_both_native_ego_modules() -> None:
    files = _project_data_files()
    for name in ("ego_host.mjs", "ego_dom.mjs"):
        member = f"openbiliclaw/sources/{name}"
        assert member in files, f"Frozen runtime is missing {member}"
        assert files[member].is_file()


def test_frozen_ego_host_imports_its_sibling_from_a_file_uri(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the native module import check")
    files = _project_data_files()
    # macOS .app paths commonly contain spaces; __file__.with_name().as_uri()
    # must resolve both modules from resources rather than the source checkout.
    frozen = tmp_path / "应用 bundle" / "openbiliclaw" / "sources"
    frozen.mkdir(parents=True)
    for name in ("ego_host.mjs", "ego_dom.mjs"):
        shutil.copyfile(files[f"openbiliclaw/sources/{name}"], frozen / name)
    module = (frozen / "ego_executor.py").with_name("ego_host.mjs").as_uri()
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "const host = await import(process.argv[1]); "
            "if (typeof host.start !== 'function') process.exit(2);",
            module,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
