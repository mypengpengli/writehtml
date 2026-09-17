"""Load trusted, repository-owned Skills for installation into each user account."""
from functools import lru_cache
from pathlib import Path
import re

import yaml


_ROOT = Path(__file__).resolve().parent / "builtin_skills"
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESOURCE_EXTS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}


def _parse_skill_markdown(path):
    markdown = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if not markdown.startswith("---\n"):
        raise RuntimeError(f"内置 Skill 缺少 YAML frontmatter：{path}")
    end = markdown.find("\n---\n", 4)
    if end < 0:
        raise RuntimeError(f"内置 Skill frontmatter 未结束：{path}")
    try:
        metadata = yaml.safe_load(markdown[4:end]) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"内置 Skill frontmatter 无法解析：{path}: {exc}") from exc
    name = metadata.get("name")
    description = metadata.get("description")
    instruction = markdown[end + 5:].strip()
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or len(name) > 64:
        raise RuntimeError(f"内置 Skill name 无效：{path}")
    if not isinstance(description, str) or not description.strip() or len(description.strip()) > 1024:
        raise RuntimeError(f"内置 Skill description 无效：{path}")
    if not instruction or len(instruction) > 8000:
        raise RuntimeError(f"内置 Skill 正文必须为 1-8000 字：{path}")
    return markdown, name, description.strip(), instruction


def _load_resources(folder):
    resources = []
    references = folder / "references"
    if not references.is_dir():
        return resources
    total = 0
    for path in sorted(references.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _RESOURCE_EXTS:
            continue
        content = path.read_text(encoding="utf-8-sig")
        if len(content) > 256_000:
            raise RuntimeError(f"内置 Skill 资料过大：{path}")
        total += len(content)
        if total > 600_000:
            raise RuntimeError(f"内置 Skill 资料总量过大：{folder}")
        resources.append({"path": path.relative_to(folder).as_posix(), "content": content})
    return resources


@lru_cache(maxsize=1)
def load_builtin_skills():
    """Return immutable source packages; uploaded Skills never enter this directory."""
    if not _ROOT.is_dir():
        return ()
    packages = []
    names = set()
    for folder in sorted(path for path in _ROOT.iterdir() if path.is_dir()):
        skill_path = folder / "SKILL.md"
        if not skill_path.is_file():
            continue
        markdown, name, description, instruction = _parse_skill_markdown(skill_path)
        if name in names:
            raise RuntimeError(f"内置 Skill name 重复：{name}")
        names.add(name)
        packages.append({
            "builtin_key": name,
            "name": name,
            "description": description,
            "instruction": instruction,
            "source_markdown": markdown,
            "resources": _load_resources(folder),
        })
    return tuple(packages)
