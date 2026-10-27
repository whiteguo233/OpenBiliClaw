"""Skill system — persona + tool-whitelist bundles for the chat agent.

Two layers live here:

- **Chat skills (M4)** — declarative ``SkillDefinition`` bundles loaded from
  ``*/SKILL.md`` files. A chat skill is a persona system prompt plus a tool
  whitelist plus a data-availability declaration; sessions bind one skill and
  may switch mid-conversation. Builtins ship in
  ``src/openbiliclaw/agent/skills_builtin/``; users drop directories into
  ``data/skills/`` to add or override skills (same ``name`` wins, logged).
- **Legacy code skills** — the ``Skill`` ABC + ``SkillRegistry`` skeleton for
  future executable capability modules. Unused by the chat agent.

SKILL.md format (no YAML dependency — a strict subset parsed by hand)::

    ---
    name: taste-companion        # required, slug: [a-z0-9][a-z0-9-]*
    title: 口味伙伴               # optional display name (defaults to name)
    description: 默认对话伙伴      # required, one line
    tools:                        # optional whitelist; inline [a, b] also ok
      - get_profile
      - write_memory
    ---
    正文是 system prompt：人设 + 可用数据与工具入口声明。

Invalid skill files are skipped with a warning — they never break startup.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

SKILL_FILE_NAME = "SKILL.md"
DEFAULT_SKILL_NAME = "taste-companion"

SkillSource = Literal["builtin", "custom"]

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FRONTMATTER_DELIMITER = "---"


class SkillParseError(ValueError):
    """Raised when a SKILL.md file is malformed."""


@dataclass(frozen=True)
class SkillDefinition:
    """One chat skill: persona prompt + tool whitelist + data declaration.

    ``tools`` is the whitelist of agent tool names the loop may dispatch
    while this skill is active; an empty tuple means "no tools". ``source``
    distinguishes shipped builtins from user ``data/skills/`` entries.
    """

    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...] = ()
    title: str = ""
    source: SkillSource = "custom"
    origin: str = ""

    @property
    def display_name(self) -> str:
        return self.title or self.name

    @property
    def builtin(self) -> bool:
        return self.source == "builtin"

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize for ``GET /api/chat/skills``."""
        return {
            "name": self.name,
            "title": self.display_name,
            "description": self.description,
            "tools": list(self.tools),
            "source": self.source,
            "builtin": self.builtin,
        }


def parse_skill_md(
    text: str,
    *,
    source: SkillSource = "custom",
    origin: str = "",
) -> SkillDefinition:
    """Parse one SKILL.md document into a ``SkillDefinition``.

    Raises ``SkillParseError`` on malformed frontmatter, missing required
    fields (``name`` / ``description``), an invalid name slug, or an empty
    persona body.
    """
    metadata, body = _split_frontmatter(text, origin=origin)
    name = str(metadata.get("name") or "").strip()
    if not name:
        raise SkillParseError(f"{origin or 'SKILL.md'}: frontmatter 缺少 name 字段")
    if not _NAME_PATTERN.match(name):
        raise SkillParseError(
            f"{origin or name}: name 必须是小写字母/数字/连字符组成的 slug，得到 {name!r}"
        )
    description = str(metadata.get("description") or "").strip()
    if not description:
        raise SkillParseError(f"{origin or name}: frontmatter 缺少 description 字段")
    system_prompt = body.strip()
    if not system_prompt:
        raise SkillParseError(f"{origin or name}: 正文（人设 prompt）不能为空")
    tools = _coerce_str_list(metadata.get("tools"))
    return SkillDefinition(
        name=name,
        description=description,
        system_prompt=system_prompt,
        tools=tuple(tools),
        title=str(metadata.get("title") or "").strip(),
        source=source,
        origin=origin,
    )


def load_skill_file(path: Path, *, source: SkillSource) -> SkillDefinition:
    """Load one SKILL.md file, raising ``SkillParseError`` on bad input."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillParseError(f"{path}: 读取失败: {exc}") from exc
    return parse_skill_md(text, source=source, origin=str(path))


def builtin_skills_dir() -> Path:
    """Directory containing the shipped builtin skills."""
    return Path(__file__).resolve().parent / "skills_builtin"


def discover_skill_files(skills_dir: Path) -> list[Path]:
    """Discover ``*/SKILL.md`` files under ``skills_dir`` (sorted, stable)."""
    if not skills_dir.is_dir():
        return []
    return sorted(skills_dir.glob(f"*/{SKILL_FILE_NAME}"))


def load_skill_catalog(
    *,
    builtin_dir: Path | None = None,
    user_dir: Path | None = None,
) -> SkillCatalog:
    """Load builtin skills, then overlay user skills from ``data/skills/``.

    A user skill whose ``name`` matches a builtin replaces it (logged).
    Unparseable files are skipped with a warning and never abort loading.
    """
    definitions: dict[str, SkillDefinition] = {}
    effective_builtin_dir = builtin_dir if builtin_dir is not None else builtin_skills_dir()
    if builtin_dir is None and not effective_builtin_dir.is_dir():
        logger.warning("Builtin skills directory missing: %s", effective_builtin_dir)
    for path in discover_skill_files(effective_builtin_dir):
        _load_into(definitions, path, source="builtin")
    if user_dir is not None:
        for path in discover_skill_files(user_dir):
            _load_into(definitions, path, source="custom")
    return SkillCatalog(tuple(definitions.values()))


def _load_into(
    definitions: dict[str, SkillDefinition],
    path: Path,
    *,
    source: SkillSource,
) -> None:
    try:
        definition = load_skill_file(path, source=source)
    except SkillParseError as exc:
        logger.warning("Skipping invalid skill file: %s", exc)
        return
    previous = definitions.get(definition.name)
    if previous is not None:
        logger.info(
            "Skill %r from %s overrides %s (%s)",
            definition.name,
            path,
            previous.source,
            previous.origin,
        )
    definitions[definition.name] = definition


class SkillCatalog:
    """Ordered collection of chat skills with lookup and prompt rendering."""

    def __init__(self, definitions: Iterable[SkillDefinition] = ()) -> None:
        self._definitions: dict[str, SkillDefinition] = {}
        for definition in definitions:
            self._definitions[definition.name] = definition

    def get(self, name: str) -> SkillDefinition | None:
        return self._definitions.get(name.strip())

    def default(self) -> SkillDefinition | None:
        """The default skill (口味伙伴), falling back to the first loaded."""
        return self._definitions.get(DEFAULT_SKILL_NAME) or next(
            iter(self._definitions.values()), None
        )

    @property
    def names(self) -> list[str]:
        return list(self._definitions.keys())

    @property
    def definitions(self) -> list[SkillDefinition]:
        return list(self._definitions.values())

    def __len__(self) -> int:
        return len(self._definitions)

    def to_public_list(self) -> list[dict[str, Any]]:
        """Serialize every skill for ``GET /api/chat/skills``."""
        default = self.default()
        return [
            {**definition.to_public_dict(), "default": definition is default}
            for definition in self._definitions.values()
        ]

    def render_switch_guide(self, current_name: str) -> str:
        """Prompt block telling the agent how to suggest a skill switch.

        Listed alongside the active skill's persona so the model knows which
        other skills exist and that it must use the ``suggest_skill`` meta
        tool rather than switching on its own.
        """
        others = [
            definition
            for definition in self._definitions.values()
            if definition.name != current_name
        ]
        if not others:
            return ""
        lines = [
            "系统中还有其他可切换的角色（skill）：",
            *[f"- {item.name}（{item.display_name}）：{item.description}" for item in others],
            (
                "如果用户的诉求明显更适合其中某个角色，调用 suggest_skill 工具提出切换建议"
                "（给出 skill 名称和理由），并用自然语言向用户解释；是否切换由用户决定，"
                "你不能自行切换。"
            ),
        ]
        return "\n".join(lines)


def _split_frontmatter(text: str, *, origin: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-delimited frontmatter from the markdown body."""
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        raise SkillParseError(f"{origin or 'SKILL.md'}: 文件必须以 --- frontmatter 开头")
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_DELIMITER:
            closing = index
            break
    if closing is None:
        raise SkillParseError(f"{origin or 'SKILL.md'}: frontmatter 缺少结尾的 ---")
    metadata = _parse_frontmatter_lines(lines[1:closing], origin=origin)
    body = "\n".join(lines[closing + 1 :])
    return metadata, body


def _parse_frontmatter_lines(lines: list[str], *, origin: str) -> dict[str, Any]:
    """Parse a strict YAML-subset: ``key: value`` scalars and ``- item`` lists.

    Inline lists (``tools: [a, b]``) are also accepted. Anything more
    complex (nesting, anchors, multi-line scalars) is rejected — keep
    SKILL.md frontmatter flat by design.
    """
    metadata: dict[str, Any] = {}
    pending_list_key: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if pending_list_key is None:
                raise SkillParseError(f"{origin}: 列表项 {stripped!r} 没有对应的键")
            items = metadata.setdefault(pending_list_key, [])
            assert isinstance(items, list)
            items.append(_unquote(stripped[2:].strip()))
            continue
        key, separator, raw_value = stripped.partition(":")
        key = key.strip()
        if not separator or not key:
            raise SkillParseError(f"{origin}: 无法解析的 frontmatter 行 {stripped!r}")
        value = raw_value.strip()
        if not value:
            metadata[key] = []
            pending_list_key = key
            continue
        pending_list_key = None
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            metadata[key] = [_unquote(item.strip()) for item in inner.split(",") if item.strip()]
        else:
            metadata[key] = _unquote(value)
    return metadata


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


# ---------------------------------------------------------------------------
# Legacy code-skill skeleton (pre-M4). Kept for future executable skills.
# ---------------------------------------------------------------------------


@dataclass
class SkillMetadata:
    """Metadata describing a skill."""

    name: str
    description: str
    version: str = "0.1.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)


class Skill(ABC):
    """Base class for all skills.

    A Skill is an independent, self-contained capability that the agent can use.
    Each skill has:
    - A name and description
    - An execute method that performs the skill's action
    - Input/output schema definitions

    To create a custom skill:
    1. Subclass Skill
    2. Implement the `execute` method
    3. Define `metadata` property
    4. Place in the skills/ directory with a SKILL.md file
    """

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """Return metadata about this skill."""
        ...

    @property
    def name(self) -> str:
        """Skill name shortcut."""
        return self.metadata.name

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Execute the skill.

        Args:
            **kwargs: Skill-specific parameters.

        Returns:
            Skill-specific result.
        """
        ...

    def describe(self) -> str:
        """Return a human-readable description for LLM context."""
        meta = self.metadata
        return f"[{meta.name}] {meta.description}"


class SkillRegistry:
    """Registry for discovering and managing skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill instance."""
        self._skills[skill.name] = skill
        logger.info("Skill registered: %s", skill.name)

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    @property
    def all_skills(self) -> list[Skill]:
        """All registered skills."""
        return list(self._skills.values())

    def describe_all(self) -> str:
        """Return descriptions of all skills (for LLM context)."""
        return "\n".join(skill.describe() for skill in self._skills.values())

    @staticmethod
    def discover_skills(skills_dir: Path) -> list[Path]:
        """Discover skill directories under the given path.

        A valid skill directory contains a SKILL.md file.

        Args:
            skills_dir: Root directory to search for skills.

        Returns:
            List of paths to SKILL.md files.
        """
        return discover_skill_files(skills_dir)
