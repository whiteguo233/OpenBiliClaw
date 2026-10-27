#!/usr/bin/env python3
"""Inventory project LLM fan-in paths; no model calls, credentials or SDK imports."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inventory(root: Path = ROOT) -> list[dict[str, str]]:
    rows = []
    for path in sorted((root / "src/openbiliclaw").rglob("*.py")):
        if "/llm/" in path.as_posix():
            continue  # Providers/transport are tested separately at the SDK seam.
        rel = path.relative_to(root).as_posix()

        class Calls(ast.NodeVisitor):
            def __init__(self, relative_name: str) -> None:
                self.rel = relative_name
                self.scope = "module"
                self.aliases: dict[str, str] = {}

            def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                old, old_aliases = self.scope, self.aliases
                self.scope, self.aliases = node.name, {}
                self.generic_visit(node)
                self.scope, self.aliases = old, old_aliases

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self.visit_FunctionDef(node)

            def visit_Assign(self, node: ast.Assign) -> None:
                value = node.value
                method = ""
                if isinstance(value, ast.Attribute):
                    method = value.attr
                elif isinstance(value, ast.Call):
                    if (
                        isinstance(value.func, ast.Name)
                        and value.func.id == "getattr"
                        and len(value.args) > 1
                        and isinstance(value.args[1], ast.Constant)
                        and isinstance(value.args[1].value, str)
                    ):
                        method = value.args[1].value
                    elif value.args and isinstance(value.args[-1], ast.Attribute):
                        method = value.args[-1].attr
                for target in node.targets:
                    if isinstance(target, ast.Name) and method:
                        self.aliases[target.id] = method
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                fn = ast.unparse(node.func)
                leaf = self.aliases.get(fn, fn.rsplit(".", 1)[-1])
                family = ""
                route = "LLMService -> sponsored SDK"
                if "complete_multimodal" in leaf:
                    family = "vision"
                elif "complete_socratic" in leaf or "complete_with_core_memory" in leaf:
                    family = "text"
                elif leaf == "complete_with_tools":
                    family = "tools"
                elif "complete_structured" in leaf or leaf == "execute_sponsored_or_structured":
                    family = "structured"
                elif leaf in {"complete_project_task", "complete"} and (
                    leaf == "complete_project_task"
                    or fn == "complete"
                    or "llm" in fn.lower()
                    or "registry" in fn.lower()
                ):
                    family, route = "text/structured", "project helper / Registry -> sponsored SDK"
                elif (
                    leaf in {"_collect_text", "_collect_text_with_last", "collect_json", "query"}
                    and "/eval/" in self.rel
                ):
                    family, route = (
                        "text/structured/tools",
                        "evaluation sponsored agent -> SDK; Claude fallback",
                    )
                elif leaf in {"embed", "embed_document", "embed_image"}:
                    family, route = (
                        "embedding_image" if leaf == "embed_image" else "embedding_text",
                        "EmbeddingService -> sponsored adapter (isolated cache)",
                    )
                elif leaf == "complete_provider" and "/api/" in self.rel:
                    family, route = "probe", "explicit configured-provider connectivity check"
                if family:
                    rows.append(
                        {
                            "file": self.rel,
                            "function": self.scope,
                            "call": fn,
                            "family": family,
                            "route": route,
                        }
                    )
                self.generic_visit(node)

        Calls(rel).visit(ast.parse(path.read_text()))
    return sorted(rows, key=lambda r: tuple(r.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    data = inventory()
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(data)} LLM/embedding call sites recorded")
