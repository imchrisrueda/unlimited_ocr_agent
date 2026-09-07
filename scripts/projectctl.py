#!/usr/bin/env python3
"""Deterministic control plane for the Jarvis AI project template.

The controller intentionally uses only the Python standard library. It never
invokes an LLM or an external integration.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable


MIN_PYTHON = (3, 11)
PROFILES = ("code", "software", "scientific")
PROFILE_RANK = {name: index for index, name in enumerate(PROFILES)}
TASK_STATES = {
    "BACKLOG",
    "PLANNED",
    "BLOCKED",
    "READY",
    "EXECUTING",
    "TESTING",
    "REVIEW",
    "CHANGES_REQUIRED",
    "ACCEPTED",
    "HUMAN_REVIEW_REQUIRED",
}
TRANSITIONS = {
    "BACKLOG": {"PLANNED", "BLOCKED"},
    "PLANNED": {"READY", "BLOCKED"},
    "READY": {"EXECUTING", "BLOCKED"},
    "EXECUTING": {"TESTING", "BLOCKED"},
    "TESTING": {"REVIEW", "CHANGES_REQUIRED", "BLOCKED"},
    "REVIEW": {"ACCEPTED", "CHANGES_REQUIRED", "HUMAN_REVIEW_REQUIRED"},
    "CHANGES_REQUIRED": {"READY", "HUMAN_REVIEW_REQUIRED"},
    "BLOCKED": {"PLANNED", "READY", "HUMAN_REVIEW_REQUIRED"},
    "ACCEPTED": set(),
    "HUMAN_REVIEW_REQUIRED": set(),
}
PROJECT_PHASE_SEQUENCE = (
    "uninitialized",
    "discovery",
    "defined",
    "planned",
    "executing",
    "validating",
    "reviewing",
    "accepted",
)
PROJECT_PHASES = {*PROJECT_PHASE_SEQUENCE, "paused", "archived"}
PROJECT_PHASE_RANK = {name: index for index, name in enumerate(PROJECT_PHASE_SEQUENCE)}
PROJECT_TRANSITIONS = {
    "uninitialized": {"discovery"},
    "discovery": {"defined", "paused", "archived"},
    "defined": {"planned", "paused", "archived"},
    "planned": {"executing", "paused", "archived"},
    "executing": {"validating", "paused", "archived"},
    "validating": {"reviewing", "executing", "paused", "archived"},
    "reviewing": {"accepted", "executing", "paused", "archived"},
    "accepted": {"archived"},
    "paused": {"discovery", "defined", "planned", "executing", "validating", "reviewing", "archived"},
    "archived": set(),
}
TASK_MINIMUM_PHASE = {
    "PLANNED": "planned",
    "READY": "planned",
    "EXECUTING": "executing",
    "TESTING": "validating",
    "REVIEW": "reviewing",
    "CHANGES_REQUIRED": "reviewing",
    "HUMAN_REVIEW_REQUIRED": "reviewing",
}
SOFTWARE_DOCUMENT_GATES = {
    "defined": {"docs/PROJECT_BRIEF.md": ("Problem", "Objective", "Scope", "Success criteria")},
    "planned": {"docs/PROJECT_PLAN.md": ("Milestones", "Validation gates")},
    "executing": {
        "docs/ARCHITECTURE.md": ("Components and boundaries", "Interfaces", "Testing architecture"),
        "docs/RISKS.md": ("Register",),
    },
}
TASK_ID_RE = re.compile(r"^TASK-[0-9]{4}$")
REVIEW_ID_RE = re.compile(r"^REVIEW-TASK-[0-9]{4}-[0-9]{3}$")
TASK_BLOCK_RE = re.compile(
    r"<!-- TASK-METADATA-BEGIN -->\s*```json\s*(\{.*?\})\s*```\s*"
    r"<!-- TASK-METADATA-END -->",
    re.DOTALL,
)
REVIEW_BLOCK_RE = re.compile(
    r"<!-- REVIEW-METADATA-BEGIN -->\s*```json\s*(\{.*?\})\s*```\s*"
    r"<!-- REVIEW-METADATA-END -->",
    re.DOTALL,
)
TASK_REQUIRED = {
    "schema_version",
    "id",
    "title",
    "status",
    "objective",
    "context",
    "dependencies",
    "scope",
    "out_of_scope",
    "requirements",
    "technical_constraints",
    "expected_files",
    "acceptance_criteria",
    "required_tests",
    "validation_commands",
    "documentation_impact",
    "executor",
    "reviewer",
    "relevant_references",
    "knowledge_impact",
    "scientific_impact",
    "do_not",
    "notes",
    "independent_review_required",
    "review_cycle",
    "created_at",
    "updated_at",
}
TASK_LIST_FIELDS = {
    "dependencies",
    "scope",
    "out_of_scope",
    "requirements",
    "technical_constraints",
    "expected_files",
    "acceptance_criteria",
    "required_tests",
    "validation_commands",
    "relevant_references",
    "do_not",
}
BASE_REQUIRED_FILES = {
    "AGENTS.md",
    "README.md",
    "START_PROJECT.md",
    ".gitignore",
    ".gemini/settings.json",
    ".ai/project.json",
    ".ai/WORKFLOW.md",
    ".ai/STATUS.md",
    ".ai/BACKLOG.md",
    ".ai/templates/TASK_TEMPLATE.md",
    ".ai/templates/REVIEW_TEMPLATE.md",
    ".ai/templates/ADR_TEMPLATE.md",
    ".ai/templates/PROJECT_BRIEF_TEMPLATE.md",
    ".ai/schemas/project.schema.json",
    ".ai/schemas/task.schema.json",
    ".ai/schemas/review.schema.json",
    ".ai/schemas/science.schema.json",
    ".ai/schemas/ro-crate-template.schema.json",
    ".ai/profiles/code/manifest.json",
    ".ai/profiles/software/manifest.json",
    ".ai/profiles/scientific/manifest.json",
    ".ai/integrations/GRAPHIFY.md",
    ".ai/integrations/COGNEE.md",
    ".ai/integrations/RO_CRATE.md",
    ".ai/integrations/MCP.md",
    "docs/BOOTSTRAP.md",
    "scripts/projectctl.py",
}
SCIENCE_FILES = {
    "objectives": "OBJ",
    "research_questions": "RQ",
    "hypotheses": "HYP",
    "assumptions": "ASM",
    "datasets": "DATA",
    "variables": "VAR",
    "methods": "METHOD",
    "experiments": "EXPERIMENT",
    "analyses": "ANALYSIS",
    "runs": "RUN",
    "results": "RESULT",
    "evidence": "EVIDENCE",
    "claims": "CLAIM",
    "conclusions": "CONCLUSION",
    "limitations": "LIMIT",
    "artifacts": "ARTIFACT",
    "literature": "SOURCE",
    "insights": "INSIGHT",
    "evidence_conflicts": "CONFLICT",
}


class ProjectError(RuntimeError):
    """Expected, user-actionable project error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    path: str
    message: str
    entity_id: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectError("FILE_NOT_FOUND", f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectError(
            "INVALID_JSON", f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def resolve_root(raw: str | None) -> Path:
    if raw:
        candidate = Path(raw).expanduser().resolve()
    else:
        candidate = Path.cwd().resolve()
        for current in (candidate, *candidate.parents):
            if (current / ".ai" / "profiles").is_dir() and (current / "scripts" / "projectctl.py").is_file():
                candidate = current
                break
    if not (candidate / ".ai" / "profiles").is_dir():
        raise ProjectError("PROJECT_ROOT_NOT_FOUND", f"No Jarvis project root found at {candidate}")
    return candidate


def safe_path(root: Path, relative: str, *, require_exists: bool = False) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ProjectError("UNSAFE_PATH", f"Manifest path must be relative and cannot contain '..': {relative}")
    root_resolved = root.resolve()
    candidate = (root_resolved / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ProjectError("UNSAFE_PATH", f"Path escapes project root: {relative}") from exc
    if require_exists and not candidate.exists():
        raise ProjectError("FILE_NOT_FOUND", f"Path does not exist: {relative}")
    return candidate


def relative_display(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def load_config(root: Path) -> dict[str, Any]:
    value = read_json(root / ".ai" / "project.json")
    if not isinstance(value, dict):
        raise ProjectError("INVALID_CONFIG", ".ai/project.json must contain an object")
    return value


def profile_chain(root: Path, target: str) -> list[tuple[Path, dict[str, Any]]]:
    if target not in PROFILE_RANK:
        raise ProjectError("INVALID_PROFILE", f"Unknown profile: {target}")
    chain: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    current: str | None = target
    while current is not None:
        if current in seen:
            raise ProjectError("PROFILE_CYCLE", f"Profile parent cycle includes {current}")
        seen.add(current)
        manifest_path = safe_path(root, f".ai/profiles/{current}/manifest.json", require_exists=True)
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("name") != current:
            raise ProjectError("INVALID_MANIFEST", f"Invalid name in {relative_display(root, manifest_path)}")
        if manifest.get("schema_version") != 1:
            raise ProjectError("INVALID_MANIFEST", f"Unsupported schema_version in {relative_display(root, manifest_path)}")
        chain.append((manifest_path.parent, manifest))
        parent = manifest.get("parent")
        if parent is not None and parent not in PROFILE_RANK:
            raise ProjectError("INVALID_MANIFEST", f"Invalid parent {parent!r} for profile {current}")
        current = parent
    chain.reverse()
    expected = list(PROFILES[: PROFILE_RANK[target] + 1])
    actual = [manifest["name"] for _, manifest in chain]
    if actual != expected:
        raise ProjectError("INVALID_MANIFEST", f"Profile chain {actual!r} does not match {expected!r}")
    return chain


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False)
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def apply_transaction(
    root: Path,
    writes: dict[Path, bytes],
    directories: Iterable[Path] = (),
    *,
    conflicts_protected: set[Path] | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    protected = conflicts_protected or set()
    changed: list[str] = []
    unchanged: list[str] = []
    conflicts: list[str] = []
    originals: dict[Path, bytes | None] = {}

    for path, data in writes.items():
        safe_path(root, relative_display(root, path))
        if path.exists():
            if path.is_symlink():
                raise ProjectError("UNSAFE_PATH", f"Refusing to replace symlink: {relative_display(root, path)}")
            current = path.read_bytes()
            if current == data:
                unchanged.append(relative_display(root, path))
                continue
            if path in protected and not overwrite:
                conflicts.append(relative_display(root, path))
            originals[path] = current
        else:
            originals[path] = None
        changed.append(relative_display(root, path))

    if conflicts:
        raise ProjectError(
            "FILE_CONFLICT",
            "Existing files differ from profile templates; no files were changed: " + ", ".join(sorted(conflicts)),
        )
    if dry_run:
        return sorted(changed), sorted(unchanged)

    created_dirs: list[Path] = []
    backup_root: Path | None = None
    if overwrite and any(path in protected and originals.get(path) is not None for path in writes):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root = safe_path(root, f".ai/backups/{stamp}")

    try:
        for directory in sorted(set(directories), key=lambda item: len(item.parts)):
            safe_path(root, relative_display(root, directory))
            if directory.exists() and directory.is_symlink():
                raise ProjectError("UNSAFE_PATH", f"Refusing to use symlink directory: {relative_display(root, directory)}")
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                created_dirs.append(directory)
        for path, data in writes.items():
            if path.read_bytes() == data if path.exists() and path.is_file() else False:
                continue
            if backup_root is not None and path in protected and originals.get(path) is not None:
                backup_path = backup_root / path.relative_to(root)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_path)
            atomic_write(path, data)
    except Exception:
        for path, original in reversed(list(originals.items())):
            try:
                if original is None:
                    if path.exists() and path.is_file():
                        path.unlink()
                else:
                    atomic_write(path, original)
            except OSError:
                pass
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    return sorted(changed), sorted(unchanged)


def task_paths(root: Path) -> list[Path]:
    directory = root / ".ai" / "tasks"
    return sorted(directory.glob("TASK-*.md")) if directory.is_dir() else []


def parse_marked_json(path: Path, pattern: re.Pattern[str], kind: str) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectError(f"INVALID_{kind}", f"{path} is not valid UTF-8") from exc
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ProjectError(
            f"INVALID_{kind}", f"{path} must contain exactly one {kind.lower()} metadata block; found {len(matches)}"
        )
    try:
        metadata = json.loads(matches[0].group(1))
    except json.JSONDecodeError as exc:
        raise ProjectError(
            f"INVALID_{kind}", f"Invalid JSON metadata in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(metadata, dict):
        raise ProjectError(f"INVALID_{kind}", f"Metadata in {path} must be an object")
    return metadata, text


def parse_task(path: Path) -> tuple[dict[str, Any], str]:
    metadata, text = parse_marked_json(path, TASK_BLOCK_RE, "TASK")
    problems = task_metadata_problems(metadata, path)
    if problems:
        raise ProjectError("INVALID_TASK", f"{path}: " + "; ".join(problems))
    return metadata, text


def task_metadata_problems(metadata: dict[str, Any], path: Path | None = None) -> list[str]:
    problems: list[str] = []
    missing = sorted(TASK_REQUIRED - set(metadata))
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    extra = sorted(set(metadata) - TASK_REQUIRED)
    if extra:
        problems.append("unknown fields: " + ", ".join(extra))
    task_id = metadata.get("id")
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        problems.append("id must match TASK-XXXX")
    elif path is not None and path.stem != task_id:
        problems.append(f"file name {path.stem} does not match id {task_id}")
    if metadata.get("schema_version") != 1:
        problems.append("schema_version must be 1")
    if metadata.get("status") not in TASK_STATES:
        problems.append(f"invalid status {metadata.get('status')!r}")
    if not isinstance(metadata.get("title"), str) or not metadata.get("title", "").strip():
        problems.append("title must be a non-empty string")
    for field in TASK_LIST_FIELDS:
        if field in metadata and (
            not isinstance(metadata[field], list) or not all(isinstance(item, str) for item in metadata[field])
        ):
            problems.append(f"{field} must be an array of strings")
    for dependency in metadata.get("dependencies", []) if isinstance(metadata.get("dependencies"), list) else []:
        if not TASK_ID_RE.fullmatch(dependency):
            problems.append(f"invalid dependency id {dependency!r}")
    if metadata.get("executor") not in {"gemini", "local", "codex", "human"}:
        problems.append("executor must be gemini, local, codex or human")
    if metadata.get("reviewer") not in {"gemini", "local", "codex", "human"}:
        problems.append("reviewer must be gemini, local, codex or human")
    if not isinstance(metadata.get("independent_review_required"), bool):
        problems.append("independent_review_required must be boolean")
    if not isinstance(metadata.get("review_cycle"), int) or metadata.get("review_cycle", -1) < 0:
        problems.append("review_cycle must be a non-negative integer")
    return problems


def replace_marked_json(text: str, pattern: re.Pattern[str], start: str, end: str, metadata: dict[str, Any]) -> str:
    replacement = f"{start}\n```json\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n```\n{end}"
    return pattern.sub(lambda _: replacement, text, count=1)


def load_tasks(root: Path, *, tolerate_invalid: bool = False) -> tuple[dict[str, dict[str, Any]], list[Diagnostic]]:
    tasks: dict[str, dict[str, Any]] = {}
    diagnostics: list[Diagnostic] = []
    for path in task_paths(root):
        try:
            metadata, _ = parse_task(path)
        except ProjectError as exc:
            if not tolerate_invalid:
                raise
            diagnostics.append(Diagnostic("ERROR", exc.code, relative_display(root, path), str(exc)))
            continue
        task_id = metadata["id"]
        if task_id in tasks:
            diagnostics.append(Diagnostic("ERROR", "DUPLICATE_TASK", relative_display(root, path), f"Duplicate {task_id}"))
        tasks[task_id] = metadata
    return tasks, diagnostics


def status_data(config: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    statuses = [task.get("status") for task in tasks.values()]
    knowledge = config.get("knowledge", {})
    return {
        "profile": config.get("project", {}).get("profile") or "uninitialized",
        "phase": config.get("project", {}).get("phase", "uninitialized"),
        "current_milestone": config.get("planning", {}).get("current_milestone"),
        "active_tasks": sum(
            status in {"PLANNED", "READY", "EXECUTING", "TESTING", "CHANGES_REQUIRED"} for status in statuses
        ),
        "blocked_tasks": statuses.count("BLOCKED"),
        "awaiting_review": statuses.count("REVIEW") + statuses.count("HUMAN_REVIEW_REQUIRED"),
        "accepted_tasks": statuses.count("ACCEPTED"),
        "knowledge": {
            "structural_graph": bool(knowledge.get("structural_graph", {}).get("enabled")),
            "semantic_memory": bool(knowledge.get("semantic_memory", {}).get("enabled")),
            "scientific_graph": bool(knowledge.get("scientific_graph", {}).get("enabled")),
            "provenance": bool(knowledge.get("provenance", {}).get("enabled")),
        },
    }


def render_status(config: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> str:
    data = status_data(config, tasks)
    milestone = data["current_milestone"] or "none"
    labels = data["knowledge"]
    next_tasks = sorted(
        (task for task in tasks.values() if task.get("status") in {"BACKLOG", "PLANNED", "READY", "CHANGES_REQUIRED"}),
        key=lambda item: item.get("id", ""),
    )
    next_section = "No queued tasks."
    if next_tasks:
        next_section = "\n".join(f"- {item['id']}: {item['title']} [{item['status']}]" for item in next_tasks[:5])
    return (
        "<!-- GENERATED BY projectctl; DO NOT EDIT -->\n"
        "# Project status\n\n"
        f"- Profile: {data['profile']}\n"
        f"- Phase: {data['phase']}\n"
        f"- Current milestone: {milestone}\n"
        f"- Active tasks: {data['active_tasks']}\n"
        f"- Blocked tasks: {data['blocked_tasks']}\n"
        f"- Awaiting review: {data['awaiting_review']}\n"
        f"- Accepted tasks: {data['accepted_tasks']}\n"
        f"- Structural graph: {'enabled' if labels['structural_graph'] else 'disabled'}\n"
        f"- Semantic memory: {'enabled' if labels['semantic_memory'] else 'disabled'}\n"
        f"- Scientific graph: {'enabled' if labels['scientific_graph'] else 'disabled'}\n"
        f"- Provenance: {'enabled' if labels['provenance'] else 'disabled'}\n\n"
        "## Next\n\n"
        f"{next_section}\n"
    )


def render_backlog(tasks: dict[str, dict[str, Any]]) -> str:
    queued = sorted(
        (task for task in tasks.values() if task.get("status") != "ACCEPTED"), key=lambda item: item.get("id", "")
    )
    lines = ["<!-- GENERATED BY projectctl; DO NOT EDIT -->", "# Backlog", ""]
    if not queued:
        lines.append("No tasks.")
    else:
        lines.extend(["| ID | Status | Title | Executor | Reviewer |", "|---|---|---|---|---|"])
        for task in queued:
            title = str(task.get("title", "")).replace("|", "\\|")
            lines.append(
                f"| {task['id']} | {task['status']} | {title} | {task['executor']} | {task['reviewer']} |"
            )
    return "\n".join(lines) + "\n"


def view_writes(root: Path, config: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> dict[Path, bytes]:
    return {
        root / ".ai" / "STATUS.md": render_status(config, tasks).encode("utf-8"),
        root / ".ai" / "BACKLOG.md": render_backlog(tasks).encode("utf-8"),
    }


def materialization(root: Path, target: str, name: str | None) -> tuple[dict[Path, bytes], set[Path], list[Path], dict[str, Any]]:
    config = copy.deepcopy(load_config(root))
    writes: dict[Path, bytes] = {}
    protected: set[Path] = set()
    directories: list[Path] = []
    for manifest_dir, manifest in profile_chain(root, target):
        deep_merge(config, manifest.get("config_patch", {}))
        for relative in manifest.get("directories", []):
            directories.append(safe_path(root, relative))
        for entry in manifest.get("files", []):
            if not isinstance(entry, dict) or set(entry) != {"source", "target"}:
                raise ProjectError("INVALID_MANIFEST", f"Invalid file entry in profile {manifest.get('name')}")
            source_rel = entry["source"]
            target_rel = entry["target"]
            if not isinstance(source_rel, str) or not isinstance(target_rel, str):
                raise ProjectError("INVALID_MANIFEST", "Manifest source and target must be strings")
            source = safe_path(manifest_dir, source_rel, require_exists=True)
            target_path = safe_path(root, target_rel)
            data = source.read_bytes()
            writes[target_path] = data
            protected.add(target_path)
    config["project"]["name"] = name or config["project"].get("name") or root.name
    config["project"]["profile"] = target
    if config["project"].get("phase") == "uninitialized":
        config["project"]["phase"] = "discovery"
    writes[root / ".ai" / "project.json"] = json_text(config).encode("utf-8")
    tasks, _ = load_tasks(root, tolerate_invalid=False)
    writes.update(view_writes(root, config, tasks))
    return writes, protected, directories, config


def print_changes(action: str, changed: list[str], unchanged: list[str], capabilities: list[str]) -> None:
    print(f"{action} complete")
    print("Changed:")
    if changed:
        for item in changed:
            print(f"  + {item}")
    else:
        print("  (none)")
    if unchanged:
        print(f"Unchanged: {len(unchanged)} file(s)")
    print("Capabilities:")
    for capability in capabilities:
        print(f"  - {capability}")


def capabilities_for(root: Path, profile: str) -> list[str]:
    capabilities: list[str] = []
    for _, manifest in profile_chain(root, profile):
        capabilities.extend(manifest.get("capabilities", []))
    return capabilities


def command_init(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    if not (root / ".git").is_dir():
        raise ProjectError("GIT_REQUIRED", "Initialize Git before activating a profile")
    config = load_config(root)
    current = config.get("project", {}).get("profile")
    if current not in {None, args.profile}:
        raise ProjectError("PROFILE_ALREADY_SET", f"Project is {current}; use upgrade instead of init")
    writes, protected, directories, _ = materialization(root, args.profile, args.name)
    changed, unchanged = apply_transaction(
        root,
        writes,
        directories,
        conflicts_protected=protected,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    print_changes("Dry run" if args.dry_run else "Initialization", changed, unchanged, capabilities_for(root, args.profile))
    return 0


def command_upgrade(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    config = load_config(root)
    current = config.get("project", {}).get("profile")
    if current is None:
        raise ProjectError("UNINITIALIZED", "Run init before upgrade")
    if args.to not in PROFILE_RANK:
        raise ProjectError("INVALID_PROFILE", f"Unknown profile: {args.to}")
    if PROFILE_RANK[args.to] < PROFILE_RANK[current]:
        raise ProjectError("DOWNGRADE_NOT_ALLOWED", f"Automatic downgrade {current} -> {args.to} is not allowed")
    writes, protected, directories, _ = materialization(root, args.to, None)
    changed, unchanged = apply_transaction(
        root,
        writes,
        directories,
        conflicts_protected=protected,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    print_changes("Dry run" if args.dry_run else "Upgrade", changed, unchanged, capabilities_for(root, args.to))
    return 0


def config_diagnostics(root: Path, config: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    path = ".ai/project.json"
    required = {"schema_version", "project", "workflow", "agents", "executors", "planning", "knowledge", "formats"}
    missing = sorted(required - set(config))
    if missing:
        diagnostics.append(Diagnostic("ERROR", "CONFIG_MISSING_FIELDS", path, ", ".join(missing)))
    if config.get("schema_version") != 1:
        diagnostics.append(Diagnostic("ERROR", "CONFIG_SCHEMA_VERSION", path, "schema_version must be 1"))
    project = config.get("project", {})
    if not isinstance(project, dict):
        diagnostics.append(Diagnostic("ERROR", "CONFIG_PROJECT", path, "project must be an object"))
        return diagnostics
    profile = project.get("profile")
    if profile not in {None, *PROFILES}:
        diagnostics.append(Diagnostic("ERROR", "CONFIG_PROFILE", path, f"invalid profile {profile!r}"))
    if project.get("phase") not in PROJECT_PHASES:
        diagnostics.append(Diagnostic("ERROR", "CONFIG_PHASE", path, f"invalid phase {project.get('phase')!r}"))
    maximum = config.get("workflow", {}).get("max_review_cycles") if isinstance(config.get("workflow"), dict) else None
    if not isinstance(maximum, int) or maximum < 1:
        diagnostics.append(Diagnostic("ERROR", "CONFIG_REVIEW_CYCLES", path, "max_review_cycles must be >= 1"))
    if profile == "scientific":
        knowledge = config.get("knowledge", {})
        if not knowledge.get("scientific_graph", {}).get("enabled"):
            diagnostics.append(Diagnostic("ERROR", "CONFIG_SCIENCE_DISABLED", path, "scientific_graph must be enabled"))
        if not knowledge.get("provenance", {}).get("enabled"):
            diagnostics.append(Diagnostic("ERROR", "CONFIG_PROVENANCE_DISABLED", path, "provenance must be enabled"))
    return diagnostics


def markdown_sections(text: str) -> dict[str, str]:
    """Return level-two Markdown section bodies keyed by heading."""
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).strip().casefold()] = text[match.end():end].strip()
    return sections


def software_document_diagnostics(root: Path, profile: str | None, phase: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if profile not in PROFILE_RANK or PROFILE_RANK[profile] < PROFILE_RANK["software"]:
        return diagnostics
    if phase not in PROJECT_PHASE_RANK:
        return diagnostics
    current_rank = PROJECT_PHASE_RANK[phase]
    for gate_phase, documents in SOFTWARE_DOCUMENT_GATES.items():
        if current_rank < PROJECT_PHASE_RANK[gate_phase]:
            continue
        for relative, required_sections in documents.items():
            path = safe_path(root, relative)
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"(?im)^Status:\s*draft\s*$", text):
                diagnostics.append(
                    Diagnostic("ERROR", "DOCUMENT_DRAFT", relative, f"Complete this document before phase {gate_phase}")
                )
            sections = markdown_sections(text)
            for heading in required_sections:
                if not sections.get(heading.casefold(), "").strip():
                    diagnostics.append(
                        Diagnostic("ERROR", "DOCUMENT_SECTION_EMPTY", relative, f"Section '{heading}' must not be empty")
                    )
    return diagnostics


def project_lifecycle_diagnostics(config: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    phase = config.get("project", {}).get("phase")
    if phase not in PROJECT_PHASE_RANK:
        return diagnostics
    phase_rank = PROJECT_PHASE_RANK[phase]
    for task_id, task in tasks.items():
        minimum = TASK_MINIMUM_PHASE.get(task.get("status"))
        if minimum and phase_rank < PROJECT_PHASE_RANK[minimum]:
            diagnostics.append(
                Diagnostic(
                    "ERROR",
                    "PROJECT_PHASE_BEHIND_TASK",
                    ".ai/project.json",
                    f"Phase {phase} is behind {task_id} status {task.get('status')}; minimum phase is {minimum}",
                    task_id,
                )
            )
    if tasks and all(task.get("status") == "ACCEPTED" for task in tasks.values()):
        minimum = "reviewing"
        if phase_rank < PROJECT_PHASE_RANK[minimum]:
            diagnostics.append(
                Diagnostic(
                    "ERROR",
                    "PROJECT_PHASE_BEHIND_TASKS",
                    ".ai/project.json",
                    f"All TASKs are accepted but project phase is {phase}; minimum phase is {minimum}",
                )
            )
    return diagnostics


def project_readiness_diagnostics(
    root: Path, config: dict[str, Any], tasks: dict[str, dict[str, Any]], target: str
) -> list[Diagnostic]:
    profile = config.get("project", {}).get("profile")
    diagnostics = software_document_diagnostics(root, profile, target)
    candidate = copy.deepcopy(config)
    candidate["project"]["phase"] = target
    diagnostics.extend(project_lifecycle_diagnostics(candidate, tasks))
    if target == "accepted":
        if not tasks:
            diagnostics.append(Diagnostic("ERROR", "PROJECT_HAS_NO_TASKS", ".ai/tasks", "Accepted requires at least one TASK"))
        incomplete = sorted(task_id for task_id, task in tasks.items() if task.get("status") != "ACCEPTED")
        if incomplete:
            diagnostics.append(
                Diagnostic("ERROR", "TASKS_NOT_ACCEPTED", ".ai/tasks", "Not accepted: " + ", ".join(incomplete))
            )
    return diagnostics


def manifest_diagnostics(root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for profile in PROFILES:
        path = root / ".ai" / "profiles" / profile / "manifest.json"
        try:
            profile_chain(root, profile)
        except ProjectError as exc:
            diagnostics.append(Diagnostic("ERROR", exc.code, relative_display(root, path), str(exc)))
    return diagnostics


def task_graph_diagnostics(root: Path, tasks: dict[str, dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for task_id, task in tasks.items():
        for dependency in task.get("dependencies", []):
            if dependency not in tasks:
                diagnostics.append(
                    Diagnostic("ERROR", "TASK_BROKEN_DEPENDENCY", f".ai/tasks/{task_id}.md", f"Unknown dependency {dependency}", task_id)
                )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str, trail: list[str]) -> None:
        if task_id in visiting:
            cycle = " -> ".join(trail + [task_id])
            diagnostics.append(Diagnostic("ERROR", "TASK_DEPENDENCY_CYCLE", f".ai/tasks/{task_id}.md", cycle, task_id))
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks.get(task_id, {}).get("dependencies", []):
            if dependency in tasks:
                visit(dependency, trail + [task_id])
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(tasks):
        visit(task_id, [])
    return diagnostics


def review_diagnostics(root: Path, tasks: dict[str, dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    review_dir = root / ".ai" / "reviews"
    for path in sorted(review_dir.glob("REVIEW-*.md")) if review_dir.is_dir() else []:
        display = relative_display(root, path)
        try:
            metadata, _ = parse_marked_json(path, REVIEW_BLOCK_RE, "REVIEW")
        except ProjectError as exc:
            diagnostics.append(Diagnostic("ERROR", exc.code, display, str(exc)))
            continue
        required = {
            "schema_version", "id", "task_id", "cycle", "executor", "reviewer", "result", "commit",
            "validation_commands", "summary", "findings", "created_at",
        }
        missing = required - set(metadata)
        if missing:
            diagnostics.append(Diagnostic("ERROR", "REVIEW_MISSING_FIELDS", display, ", ".join(sorted(missing))))
        if not REVIEW_ID_RE.fullmatch(str(metadata.get("id", ""))) or path.stem != metadata.get("id"):
            diagnostics.append(Diagnostic("ERROR", "REVIEW_ID", display, "Review id and file name must match"))
        if metadata.get("task_id") not in tasks:
            diagnostics.append(Diagnostic("ERROR", "REVIEW_BROKEN_TASK", display, f"Unknown task {metadata.get('task_id')!r}"))
        if metadata.get("result") not in {"PASS", "CHANGES_REQUIRED", "HUMAN_REVIEW_REQUIRED"}:
            diagnostics.append(Diagnostic("ERROR", "REVIEW_RESULT", display, "Invalid review result"))
    return diagnostics


def validate_project(root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not (root / ".git").is_dir():
        diagnostics.append(Diagnostic("ERROR", "GIT_REQUIRED", ".git", "Project is not a Git repository"))
    for relative in sorted(BASE_REQUIRED_FILES):
        if not safe_path(root, relative).is_file():
            diagnostics.append(Diagnostic("ERROR", "REQUIRED_FILE_MISSING", relative, "Required base file is missing"))
    for json_path in sorted((root / ".ai" / "schemas").glob("*.json")) if (root / ".ai" / "schemas").is_dir() else []:
        try:
            read_json(json_path)
        except ProjectError as exc:
            diagnostics.append(Diagnostic("ERROR", exc.code, relative_display(root, json_path), str(exc)))
    settings_path = root / ".gemini" / "settings.json"
    if settings_path.is_file():
        try:
            settings = read_json(settings_path)
            names = settings.get("context", {}).get("fileName", []) if isinstance(settings, dict) else []
            if "AGENTS.md" not in names:
                diagnostics.append(Diagnostic("ERROR", "GEMINI_CONTEXT", ".gemini/settings.json", "AGENTS.md is not configured"))
        except ProjectError as exc:
            diagnostics.append(Diagnostic("ERROR", exc.code, ".gemini/settings.json", str(exc)))
    try:
        config = load_config(root)
    except ProjectError as exc:
        diagnostics.append(Diagnostic("ERROR", exc.code, ".ai/project.json", str(exc)))
        return diagnostics
    diagnostics.extend(config_diagnostics(root, config))
    diagnostics.extend(manifest_diagnostics(root))
    profile = config.get("project", {}).get("profile")
    if profile in PROFILE_RANK:
        for _, manifest in profile_chain(root, profile):
            for relative in manifest.get("directories", []):
                if not safe_path(root, relative).is_dir():
                    diagnostics.append(Diagnostic("ERROR", "REQUIRED_DIRECTORY_MISSING", relative, "Profile directory is missing"))
            for entry in manifest.get("files", []):
                target = entry.get("target", "")
                if not safe_path(root, target).is_file():
                    diagnostics.append(Diagnostic("ERROR", "REQUIRED_FILE_MISSING", target, f"Required by profile {profile}"))
    tasks, task_parse_diagnostics = load_tasks(root, tolerate_invalid=True)
    diagnostics.extend(task_parse_diagnostics)
    diagnostics.extend(task_graph_diagnostics(root, tasks))
    diagnostics.extend(review_diagnostics(root, tasks))
    diagnostics.extend(project_lifecycle_diagnostics(config, tasks))
    diagnostics.extend(software_document_diagnostics(root, profile, config.get("project", {}).get("phase", "uninitialized")))
    expected_status = render_status(config, tasks)
    expected_backlog = render_backlog(tasks)
    for relative, expected in ((".ai/STATUS.md", expected_status), (".ai/BACKLOG.md", expected_backlog)):
        path = root / relative
        if path.is_file() and path.read_text(encoding="utf-8") != expected:
            diagnostics.append(Diagnostic("ERROR", "GENERATED_VIEW_STALE", relative, "Run projectctl status --write"))
    if profile == "scientific":
        diagnostics.extend(science_diagnostics(root))
    return sorted(diagnostics, key=lambda item: ({"ERROR": 0, "WARNING": 1, "INFO": 2}.get(item.severity, 9), item.path, item.entity_id or "", item.code))


def print_diagnostics(diagnostics: list[Diagnostic], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps({"schema_version": 1, "diagnostics": [asdict(item) for item in diagnostics]}, ensure_ascii=False, indent=2))
        return
    if not diagnostics:
        print("PASS: no diagnostics")
        return
    for item in diagnostics:
        entity = f" [{item.entity_id}]" if item.entity_id else ""
        print(f"{item.severity} {item.code} {item.path}{entity}: {item.message}")
    counts = {severity: sum(item.severity == severity for item in diagnostics) for severity in ("ERROR", "WARNING", "INFO")}
    print(f"Summary: {counts['ERROR']} error(s), {counts['WARNING']} warning(s), {counts['INFO']} info")


def command_validate(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    diagnostics = validate_project(root)
    print_diagnostics(diagnostics, args.format)
    errors = any(item.severity == "ERROR" for item in diagnostics)
    warnings = any(item.severity == "WARNING" for item in diagnostics)
    return 1 if errors or (args.strict and warnings) else 0


def command_status(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    config = load_config(root)
    tasks, diagnostics = load_tasks(root, tolerate_invalid=True)
    if diagnostics:
        print_diagnostics(diagnostics, args.format)
        return 1
    if args.write:
        apply_transaction(root, view_writes(root, config, tasks))
    data = status_data(config, tasks)
    if config.get("project", {}).get("profile") == "scientific":
        science = science_diagnostics(root)
        data["scientific_lint"] = {
            "errors": sum(item.severity == "ERROR" for item in science),
            "warnings": sum(item.severity == "WARNING" for item in science),
        }
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_status(config, tasks), end="")
    return 0


def command_project_transition(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    config = load_config(root)
    current = config.get("project", {}).get("phase")
    target = args.to
    if target not in PROJECT_TRANSITIONS.get(current, set()):
        raise ProjectError("INVALID_PROJECT_TRANSITION", f"Transition {current} -> {target} is not allowed")
    tasks, task_diagnostics = load_tasks(root, tolerate_invalid=True)
    if task_diagnostics:
        print_diagnostics(task_diagnostics, "human")
        return 1
    readiness = project_readiness_diagnostics(root, config, tasks, target)
    if readiness:
        print_diagnostics(readiness, "human")
        return 1
    updated = copy.deepcopy(config)
    updated["project"]["phase"] = target
    writes = {
        root / ".ai" / "project.json": json_text(updated).encode("utf-8"),
        **view_writes(root, updated, tasks),
    }
    apply_transaction(root, writes)
    print(f"Project: {current} -> {target}")
    return 0


def find_task(root: Path, task_id: str) -> Path:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ProjectError("INVALID_TASK_ID", "TASK id must match TASK-XXXX")
    path = safe_path(root, f".ai/tasks/{task_id}.md")
    if not path.is_file():
        raise ProjectError("TASK_NOT_FOUND", f"Task does not exist: {task_id}")
    return path


def command_task_transition(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    path = find_task(root, args.task_id)
    metadata, text = parse_task(path)
    current = metadata["status"]
    target = args.to
    if target == "ACCEPTED":
        raise ProjectError("REVIEW_REQUIRED", "ACCEPTED can only be produced by review record --result pass")
    if target not in TRANSITIONS.get(current, set()):
        raise ProjectError("INVALID_TRANSITION", f"Transition {current} -> {target} is not allowed")
    metadata["status"] = target
    metadata["updated_at"] = utc_now()
    if args.reason:
        metadata["notes"] = (metadata.get("notes", "") + f"\n[{metadata['updated_at']}] {args.reason}").strip()
    new_text = replace_marked_json(
        text, TASK_BLOCK_RE, "<!-- TASK-METADATA-BEGIN -->", "<!-- TASK-METADATA-END -->", metadata
    )
    config = load_config(root)
    tasks, _ = load_tasks(root)
    tasks[args.task_id] = metadata
    writes = {path: new_text.encode("utf-8"), **view_writes(root, config, tasks)}
    apply_transaction(root, writes)
    print(f"{args.task_id}: {current} -> {target}")
    return 0


def next_review_id(root: Path, task_id: str) -> str:
    review_dir = root / ".ai" / "reviews"
    numbers: list[int] = []
    if review_dir.is_dir():
        pattern = re.compile(rf"^REVIEW-{re.escape(task_id)}-([0-9]{{3}})\.md$")
        for path in review_dir.iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                numbers.append(int(match.group(1)))
    return f"REVIEW-{task_id}-{max(numbers, default=0) + 1:03d}"


def command_review_record(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    task_path = find_task(root, args.task_id)
    task, task_text = parse_task(task_path)
    if task["status"] != "REVIEW":
        raise ProjectError("TASK_NOT_IN_REVIEW", f"{args.task_id} is {task['status']}, not REVIEW")
    reviewer = args.reviewer or task["reviewer"]
    requested = args.result
    maximum = load_config(root).get("workflow", {}).get("max_review_cycles", 3)
    cycle = int(task.get("review_cycle", 0))
    if requested == "pass":
        if task.get("independent_review_required") and reviewer == task.get("executor"):
            final_status = "HUMAN_REVIEW_REQUIRED"
            result = "HUMAN_REVIEW_REQUIRED"
            summary = args.summary or "Independent review required because executor and reviewer are the same."
        else:
            final_status = "ACCEPTED"
            result = "PASS"
            summary = args.summary
    elif requested == "changes-required":
        if cycle >= maximum:
            final_status = "HUMAN_REVIEW_REQUIRED"
            result = "HUMAN_REVIEW_REQUIRED"
            summary = args.summary or "Maximum correction cycles exhausted."
        else:
            cycle += 1
            final_status = "CHANGES_REQUIRED"
            result = "CHANGES_REQUIRED"
            summary = args.summary
    else:
        final_status = "HUMAN_REVIEW_REQUIRED"
        result = "HUMAN_REVIEW_REQUIRED"
        summary = args.summary
    review_id = next_review_id(root, args.task_id)
    created = utc_now()
    review = {
        "schema_version": 1,
        "id": review_id,
        "task_id": args.task_id,
        "cycle": cycle,
        "executor": task["executor"],
        "reviewer": reviewer,
        "result": result,
        "commit": args.commit,
        "validation_commands": args.validation_command or [],
        "summary": summary or "",
        "findings": args.finding or [],
        "created_at": created,
    }
    review_text = (
        f"# {review_id}\n\n"
        "<!-- REVIEW-METADATA-BEGIN -->\n"
        f"```json\n{json.dumps(review, ensure_ascii=False, indent=2)}\n```\n"
        "<!-- REVIEW-METADATA-END -->\n"
    )
    task["status"] = final_status
    task["review_cycle"] = cycle
    task["updated_at"] = created
    updated_task_text = replace_marked_json(
        task_text, TASK_BLOCK_RE, "<!-- TASK-METADATA-BEGIN -->", "<!-- TASK-METADATA-END -->", task
    )
    config = load_config(root)
    tasks, _ = load_tasks(root)
    tasks[args.task_id] = task
    review_path = safe_path(root, f".ai/reviews/{review_id}.md")
    writes = {
        review_path: review_text.encode("utf-8"),
        task_path: updated_task_text.encode("utf-8"),
        **view_writes(root, config, tasks),
    }
    apply_transaction(root, writes, [review_path.parent], conflicts_protected={review_path})
    print(f"{review_id}: {result}; {args.task_id} -> {final_status}")
    return 0


def load_science(root: Path) -> tuple[dict[str, list[dict[str, Any]]], list[Diagnostic]]:
    records: dict[str, list[dict[str, Any]]] = {}
    diagnostics: list[Diagnostic] = []
    base = root / "knowledge" / "science"
    for kind in SCIENCE_FILES:
        path = base / f"{kind}.json"
        display = relative_display(root, path)
        if not path.is_file():
            diagnostics.append(Diagnostic("ERROR", "SCI_FILE_MISSING", display, "Required scientific file is missing"))
            records[kind] = []
            continue
        try:
            envelope = read_json(path)
        except ProjectError as exc:
            diagnostics.append(Diagnostic("ERROR", exc.code, display, str(exc)))
            records[kind] = []
            continue
        if not isinstance(envelope, dict) or envelope.get("schema_version") != 1 or not isinstance(envelope.get("items"), list):
            diagnostics.append(
                Diagnostic("ERROR", "SCI_INVALID_ENVELOPE", display, "Expected schema_version 1 and an items array")
            )
            records[kind] = []
            continue
        valid_items: list[dict[str, Any]] = []
        for index, item in enumerate(envelope["items"]):
            if not isinstance(item, dict):
                diagnostics.append(Diagnostic("ERROR", "SCI_INVALID_ITEM", display, f"items[{index}] must be an object"))
            else:
                valid_items.append(item)
        records[kind] = valid_items
    return records, diagnostics


def values_as_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def science_diagnostics(root: Path) -> list[Diagnostic]:
    records, diagnostics = load_science(root)
    base = root / "knowledge" / "science"
    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    all_current_ids: set[str] = set()
    for kind, items in records.items():
        prefix = SCIENCE_FILES[kind]
        path = relative_display(root, base / f"{kind}.json")
        for item in items:
            entity_id = item.get("id")
            if not isinstance(entity_id, str) or not re.fullmatch(rf"{re.escape(prefix)}-[0-9]{{3,}}", entity_id):
                diagnostics.append(
                    Diagnostic("ERROR", "SCI_INVALID_ID", path, f"ID must match {prefix}-NNN", str(entity_id) if entity_id else None)
                )
                continue
            if entity_id in by_id:
                diagnostics.append(Diagnostic("ERROR", "SCI_DUPLICATE_ID", path, "ID is used more than once", entity_id))
            by_id[entity_id] = (kind, item)
            all_current_ids.add(entity_id)

    registry_path = base / "id_registry.json"
    issued: set[str] = set()
    tombstones: set[str] = set()
    if registry_path.is_file():
        try:
            registry = read_json(registry_path)
            issued_raw = registry.get("issued_ids", []) if isinstance(registry, dict) else []
            tombstones_raw = registry.get("tombstones", []) if isinstance(registry, dict) else []
            if not isinstance(issued_raw, list) or not all(isinstance(item, str) for item in issued_raw):
                diagnostics.append(Diagnostic("ERROR", "SCI_INVALID_REGISTRY", relative_display(root, registry_path), "issued_ids must be strings"))
            else:
                issued = set(issued_raw)
            if not isinstance(tombstones_raw, list) or not all(isinstance(item, str) for item in tombstones_raw):
                diagnostics.append(Diagnostic("ERROR", "SCI_INVALID_REGISTRY", relative_display(root, registry_path), "tombstones must be strings"))
            else:
                tombstones = set(tombstones_raw)
        except ProjectError as exc:
            diagnostics.append(Diagnostic("ERROR", exc.code, relative_display(root, registry_path), str(exc)))
    for entity_id in sorted(all_current_ids - issued):
        kind = by_id[entity_id][0]
        diagnostics.append(
            Diagnostic("ERROR", "SCI_ID_NOT_REGISTERED", f"knowledge/science/{kind}.json", "Active ID is absent from id_registry", entity_id)
        )
    for entity_id in sorted(all_current_ids & tombstones):
        kind = by_id[entity_id][0]
        diagnostics.append(
            Diagnostic("ERROR", "SCI_REUSED_ID", f"knowledge/science/{kind}.json", "Tombstoned ID cannot be active", entity_id)
        )
    for entity_id in sorted(tombstones - issued):
        diagnostics.append(
            Diagnostic("ERROR", "SCI_INVALID_REGISTRY", "knowledge/science/id_registry.json", "Tombstone was never issued", entity_id)
        )

    reference_fields = {
        "research_questions": ["objective_id", "objective_ids"],
        "hypotheses": ["research_question_id", "research_question_ids"],
        "experiments": ["hypothesis_id", "hypothesis_ids", "dataset_ids", "method_id", "method_ids"],
        "analyses": ["hypothesis_id", "hypothesis_ids", "dataset_ids", "method_id", "method_ids", "experiment_id"],
        "runs": ["analysis_id", "dataset_ids", "method_id", "method_ids", "agent_ids"],
        "results": ["analysis_id", "analysis_ids", "run_id", "dataset_ids", "method_id", "method_ids", "limitation_ids", "artifact_ids"],
        "evidence": ["result_id", "claim_id", "limitation_ids"],
        "claims": ["assumption_ids", "limitation_ids", "source_ids"],
        "conclusions": ["claim_id", "claim_ids", "limitation_ids"],
        "limitations": ["applies_to_ids"],
        "artifacts": ["result_id", "result_ids", "run_id", "analysis_id"],
        "literature": ["supports", "contradicts", "background_for"],
        "insights": ["derived_from_ids"],
        "evidence_conflicts": ["claim_id", "evidence_ids"],
    }
    for kind, fields in reference_fields.items():
        path = f"knowledge/science/{kind}.json"
        for item in records.get(kind, []):
            entity_id = item.get("id") if isinstance(item.get("id"), str) else None
            for field in fields:
                for reference in values_as_ids(item.get(field)):
                    if reference not in by_id:
                        diagnostics.append(
                            Diagnostic("ERROR", "SCI_BROKEN_ID", path, f"{field} references unknown {reference}", entity_id)
                        )

    objective_ids = {item.get("id") for item in records.get("objectives", [])}
    for item in records.get("research_questions", []):
        links = values_as_ids(item.get("objective_id")) + values_as_ids(item.get("objective_ids"))
        if item.get("status", "active") != "draft" and not any(link in objective_ids for link in links):
            diagnostics.append(
                Diagnostic("ERROR", "SCI_ORPHAN_RQ", "knowledge/science/research_questions.json", "Active research question has no objective", item.get("id"))
            )

    analyses = records.get("analyses", [])
    analysed_hypotheses = {
        reference
        for analysis in analyses
        for field in ("hypothesis_id", "hypothesis_ids")
        for reference in values_as_ids(analysis.get(field))
        if analysis.get("status", "planned") in {"planned", "active", "complete"}
    }
    for item in records.get("hypotheses", []):
        if item.get("status", "active") in {"active", "supported", "refuted", "unresolved"} and item.get("id") not in analysed_hypotheses:
            diagnostics.append(
                Diagnostic("ERROR", "SCI_HYP_NO_ANALYSIS", "knowledge/science/hypotheses.json", "Active hypothesis has no planned or completed analysis", item.get("id"))
            )
    for item in analyses:
        if item.get("analysis_type", "empirical") == "empirical" and not values_as_ids(item.get("dataset_ids")):
            diagnostics.append(
                Diagnostic("ERROR", "SCI_ANALYSIS_NO_DATASET", "knowledge/science/analyses.json", "Empirical analysis has no dataset", item.get("id"))
            )

    used_datasets: set[str] = set()
    for kind in ("experiments", "analyses", "runs", "results"):
        for item in records.get(kind, []):
            used_datasets.update(values_as_ids(item.get("dataset_ids")))
    for item in records.get("datasets", []):
        if item.get("id") not in used_datasets:
            diagnostics.append(
                Diagnostic("WARNING", "SCI_UNUSED_DATASET", "knowledge/science/datasets.json", "Dataset is not referenced", item.get("id"))
            )

    for item in records.get("results", []):
        result_id = item.get("id")
        analyses_links = values_as_ids(item.get("analysis_id")) + values_as_ids(item.get("analysis_ids"))
        if not analyses_links:
            diagnostics.append(
                Diagnostic("ERROR", "SCI_RESULT_NO_ANALYSIS", "knowledge/science/results.json", "Result has no analysis", result_id)
            )
        computational = item.get("provenance_required") is True or item.get("generation_mode") == "computational" or item.get("result_type") in {"computed", "computational"}
        if computational and not item.get("run_id"):
            diagnostics.append(
                Diagnostic("ERROR", "SCI_RESULT_NO_RUN", "knowledge/science/results.json", "Computational result has no RUN", result_id)
            )

    required_run_fields = {
        "timestamp", "git_commit", "git_dirty", "environment", "inputs", "input_hashes", "parameters",
        "software_versions", "outputs", "output_hashes", "status",
    }
    required_run_ids = {item.get("run_id") for item in records.get("results", []) if item.get("run_id")}
    for item in records.get("runs", []):
        if item.get("id") in required_run_ids:
            missing = sorted(field for field in required_run_fields if field not in item or item.get(field) is None)
            if missing:
                diagnostics.append(
                    Diagnostic("ERROR", "SCI_RUN_PROVENANCE_GAP", "knowledge/science/runs.json", "Missing: " + ", ".join(missing), item.get("id"))
                )

    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    for item in records.get("evidence", []):
        relation = item.get("relation")
        if relation not in {"supports", "contradicts"}:
            diagnostics.append(
                Diagnostic("ERROR", "SCI_EVIDENCE_RELATION", "knowledge/science/evidence.json", "relation must be supports or contradicts", item.get("id"))
            )
        claim_id = item.get("claim_id")
        if isinstance(claim_id, str):
            evidence_by_claim.setdefault(claim_id, []).append(item)
    for item in records.get("claims", []):
        claim_id = item.get("id")
        if item.get("claim_type", "empirical") == "empirical" and not evidence_by_claim.get(str(claim_id)):
            diagnostics.append(
                Diagnostic("ERROR", "SCI_EMPIRICAL_CLAIM_NO_EVIDENCE", "knowledge/science/claims.json", "Empirical claim has no Evidence", claim_id)
            )
        if item.get("claim_type", "empirical") == "empirical" and not item.get("scope"):
            diagnostics.append(
                Diagnostic("ERROR", "SCI_SCOPE_UNDECLARED", "knowledge/science/claims.json", "Empirical claim has no declared scope", claim_id)
            )
        if item.get("claim_type") == "conceptual" and not (values_as_ids(item.get("source_ids")) or values_as_ids(item.get("assumption_ids"))):
            diagnostics.append(
                Diagnostic("WARNING", "SCI_CONCEPTUAL_CLAIM_NO_SOURCE", "knowledge/science/claims.json", "Conceptual claim has no source or assumption", claim_id)
            )

    acknowledged_claims = {
        item.get("claim_id")
        for item in records.get("evidence_conflicts", [])
        if item.get("status") in {"acknowledged", "resolved"}
    }
    for claim_id, items in evidence_by_claim.items():
        relations = {item.get("relation") for item in items}
        if {"supports", "contradicts"}.issubset(relations) and claim_id not in acknowledged_claims:
            diagnostics.append(
                Diagnostic("ERROR", "SCI_EVIDENCE_CONFLICT_UNACKNOWLEDGED", "knowledge/science/evidence.json", "Supporting and contradicting evidence require a conflict record", claim_id)
            )

    for item in records.get("conclusions", []):
        conclusion_id = item.get("id")
        claim_links = values_as_ids(item.get("claim_id")) + values_as_ids(item.get("claim_ids"))
        if not claim_links:
            diagnostics.append(
                Diagnostic("ERROR", "SCI_CONCLUSION_NO_CLAIM", "knowledge/science/conclusions.json", "Conclusion has no Claim", conclusion_id)
            )
        if item.get("status") == "accepted" and not item.get("scope"):
            diagnostics.append(
                Diagnostic("ERROR", "SCI_SCOPE_UNDECLARED", "knowledge/science/conclusions.json", "Accepted conclusion has no declared scope", conclusion_id)
            )
        if item.get("status") == "accepted" and item.get("limitations_assessed") is not True:
            diagnostics.append(
                Diagnostic("ERROR", "SCI_LIMITATIONS_NOT_ASSESSED", "knowledge/science/conclusions.json", "Accepted conclusion must assess limitations", conclusion_id)
            )
        if item.get("scope"):
            diagnostics.append(
                Diagnostic("WARNING", "SCI_SCOPE_NEEDS_AUDIT", "knowledge/science/conclusions.json", "Scope requires reasoned audit unless an explicit taxonomy proves inclusion", conclusion_id)
            )
    for item in records.get("assumptions", []):
        if item.get("status") == "unresolved":
            diagnostics.append(
                Diagnostic("WARNING", "SCI_UNRESOLVED_ASSUMPTION", "knowledge/science/assumptions.json", "Assumption remains unresolved", item.get("id"))
            )
    return sorted(diagnostics, key=lambda item: ({"ERROR": 0, "WARNING": 1, "INFO": 2}.get(item.severity, 9), item.path, item.entity_id or "", item.code))


def command_science_lint(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    config = load_config(root)
    if config.get("project", {}).get("profile") != "scientific":
        raise ProjectError("SCIENTIFIC_PROFILE_REQUIRED", "science lint requires the scientific profile")
    diagnostics = science_diagnostics(root)
    print_diagnostics(diagnostics, args.format)
    errors = any(item.severity == "ERROR" for item in diagnostics)
    warnings = any(item.severity == "WARNING" for item in diagnostics)
    return 1 if errors or (args.strict and warnings) else 0


def add_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="Project root; defaults to discovery from the current directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="projectctl", description=__doc__)
    parser.add_argument("--version", action="version", version="projectctl 0.2.0")
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="Activate an initial profile")
    init_parser.add_argument("--profile", required=True, choices=PROFILES)
    init_parser.add_argument("--name")
    init_parser.add_argument("--overwrite", action="store_true", help="Back up and replace conflicting profile files")
    init_parser.add_argument("--dry-run", action="store_true")
    add_root_argument(init_parser)
    init_parser.set_defaults(handler=command_init)

    upgrade_parser = commands.add_parser("upgrade", help="Upgrade to a higher cumulative profile")
    upgrade_parser.add_argument("--to", required=True, choices=PROFILES)
    upgrade_parser.add_argument("--overwrite", action="store_true")
    upgrade_parser.add_argument("--dry-run", action="store_true")
    add_root_argument(upgrade_parser)
    upgrade_parser.set_defaults(handler=command_upgrade)

    status_parser = commands.add_parser("status", help="Show project state")
    status_parser.add_argument("--write", action="store_true", help="Regenerate STATUS and BACKLOG")
    status_parser.add_argument("--format", choices=("human", "json"), default="human")
    add_root_argument(status_parser)
    status_parser.set_defaults(handler=command_status)

    validate_parser = commands.add_parser("validate", help="Validate deterministic project invariants")
    validate_parser.add_argument("--format", choices=("human", "json"), default="human")
    validate_parser.add_argument("--strict", action="store_true")
    add_root_argument(validate_parser)
    validate_parser.set_defaults(handler=command_validate)

    project_parser = commands.add_parser("project", help="Project lifecycle operations")
    project_commands = project_parser.add_subparsers(dest="project_command", required=True)
    project_transition_parser = project_commands.add_parser("transition", help="Apply an allowed project phase transition")
    project_transition_parser.add_argument("--to", required=True, choices=sorted(PROJECT_PHASES - {"uninitialized"}))
    add_root_argument(project_transition_parser)
    project_transition_parser.set_defaults(handler=command_project_transition)

    task_parser = commands.add_parser("task", help="TASK operations")
    task_commands = task_parser.add_subparsers(dest="task_command", required=True)
    transition_parser = task_commands.add_parser("transition", help="Apply an allowed state transition")
    transition_parser.add_argument("task_id")
    transition_parser.add_argument("--to", required=True, choices=sorted(TASK_STATES))
    transition_parser.add_argument("--reason")
    add_root_argument(transition_parser)
    transition_parser.set_defaults(handler=command_task_transition)

    review_parser = commands.add_parser("review", help="Review operations")
    review_commands = review_parser.add_subparsers(dest="review_command", required=True)
    record_parser = review_commands.add_parser("record", help="Record an immutable review")
    record_parser.add_argument("task_id")
    record_parser.add_argument("--result", choices=("pass", "changes-required", "human-review-required"), required=True)
    record_parser.add_argument("--reviewer")
    record_parser.add_argument("--summary", default="")
    record_parser.add_argument("--commit")
    record_parser.add_argument("--validation-command", action="append")
    record_parser.add_argument("--finding", action="append")
    add_root_argument(record_parser)
    record_parser.set_defaults(handler=command_review_record)

    science_parser = commands.add_parser("science", help="Scientific knowledge operations")
    science_commands = science_parser.add_subparsers(dest="science_command", required=True)
    lint_parser = science_commands.add_parser("lint", help="Run structural scientific lint")
    lint_parser.add_argument("--format", choices=("human", "json"), default="human")
    lint_parser.add_argument("--strict", action="store_true")
    add_root_argument(lint_parser)
    lint_parser.set_defaults(handler=command_science_lint)
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        print("ERROR PYTHON_VERSION: projectctl requires CPython 3.11 or newer", file=sys.stderr)
        return 2
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ProjectError as exc:
        print(f"ERROR {exc.code}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR INTERRUPTED: operation cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
