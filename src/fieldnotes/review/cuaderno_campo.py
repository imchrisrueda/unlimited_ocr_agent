"""Human review and publication contract for ``cuaderno_campo`` sessions."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PAGE_HEADING = re.compile(r"(?m)^## Página ([1-9]\d*)\s*$")
SUBHEADING = re.compile(r"(?m)^### (.+?)\s*$")
SPATIAL_INTERPRETATION = re.compile(
    r"(?ms)^#### Interpretación espacial(?: revisada| propuesta por el VLM)?\s*$\n(.*?)(?=^#{1,4} |\Z)"
)
PAGE_IMAGE = re.compile(r"!\[Página ([1-9]\d*)\]\(\.\./pages/page_([0-9]{3})\.(?:png|jpe?g|webp|tiff?)\)", re.IGNORECASE)


def write_atomic_file(target: Path, content: str) -> None:
    """Write UTF-8 text beside its destination and replace it atomically."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    temporary.replace(target)


class ReviewedSection(BaseModel):
    title: str
    content_markdown: str = ""
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewedPage(BaseModel):
    page_number: int = Field(ge=1)
    source_image: str
    content_markdown: str
    sections: list[ReviewedSection] = Field(default_factory=list)
    spatial_interpretation: str | None = None
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewedCuadernoDocument(BaseModel):
    schema_version: int = 1
    profile: str = "cuaderno_campo"
    authority: str = "human_reviewed"
    review_status: str = "approved"
    reviewer: str
    reviewed_at: str
    source_file: str
    machine_document: str = "../document.json"
    reviewed_markdown: str = "notas.md"
    source_sha256: str | None = None
    reviewed_markdown_sha256: str
    pages: list[ReviewedPage]
    accepted_uncertainties: list[dict[str, Any]] = Field(default_factory=list)
    model_guidance: list[str]
    model_config = ConfigDict(extra="forbid", strict=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_review_draft(markdown: str, review_dir: Path) -> None:
    """Create the editable review copy and pending manifest without overwriting edits."""
    review_dir.mkdir(parents=True, exist_ok=True)
    draft = review_dir / "transcripcion.md"
    if not draft.exists():
        review_markdown = markdown.replace("](pages/", "](../pages/").replace("](assets/", "](../assets/")
        write_atomic_file(draft, review_markdown)
    manifest = review_dir / "revision.json"
    if not manifest.exists():
        write_atomic_file(manifest, json.dumps({
            "schema_version": 1,
            "status": "pending",
            "instructions": "Corregir transcripcion.md contra pages/page_NNN.*. Las reconstrucciones espaciales propuestas por el VLM también deben verificarse y corregirse antes de publicar con scripts/publish_cuaderno_review.py.",
            "source_artifact": "../cuaderno_campo.md",
            "review_artifact": "transcripcion.md",
        }, ensure_ascii=False, indent=2) + "\n")
    resolutions = review_dir / "resolutions.json"
    issues_path = review_dir / "issues.json"
    if not resolutions.exists() and issues_path.is_file():
        issues = json.loads(issues_path.read_text(encoding="utf-8"))
        if issues:
            write_atomic_file(resolutions, json.dumps([
                {"issue_id": issue["issue_id"], "status": "pending", "note": ""}
                for issue in issues
            ], ensure_ascii=False, indent=2) + "\n")


def archive_previous_review(canonical_dir: Path, staging_review_dir: Path) -> None:
    """Preserve human work from a prior run as non-authoritative history."""
    previous_review = canonical_dir / "review"
    previous_published = canonical_dir / "reviewed"
    candidates = [
        previous_review / "transcripcion.md",
        previous_review / "resolutions.json",
        previous_review / "revision.json",
    ]
    if not previous_published.exists() and not any(path.exists() for path in candidates):
        return
    history = staging_review_dir / "history"
    old_history = previous_review / "history"
    if old_history.is_dir():
        shutil.copytree(old_history, history, dirs_exist_ok=True)
    identity_file = previous_published / "document.json"
    if not identity_file.is_file():
        identity_file = previous_review / "transcripcion.md"
    identity = _sha256(identity_file)[:12] if identity_file.is_file() else "previous"
    destination = history / identity
    destination.mkdir(parents=True, exist_ok=True)
    for source in candidates:
        if source.is_file():
            shutil.copy2(source, destination / source.name)
    if previous_published.is_dir():
        shutil.copytree(previous_published, destination / "reviewed", dirs_exist_ok=True)
    write_atomic_file(destination / "README.md", "Esta revisión pertenece a una ejecución anterior y no es la revisión vigente.\n")


def _validated_resolutions(session: Path) -> list[dict[str, Any]]:
    issues_path = session / "review" / "issues.json"
    issues = json.loads(issues_path.read_text(encoding="utf-8")) if issues_path.is_file() else []
    if not issues:
        return []
    resolutions_path = session / "review" / "resolutions.json"
    if not resolutions_path.is_file():
        raise ValueError("Existen incidencias y falta review/resolutions.json.")
    resolutions = json.loads(resolutions_path.read_text(encoding="utf-8"))
    by_id = {item.get("issue_id"): item for item in resolutions}
    expected = {issue["issue_id"] for issue in issues}
    if set(by_id) != expected:
        raise ValueError("resolutions.json debe contener exactamente una resolución por issue_id.")
    accepted: list[dict[str, Any]] = []
    for issue in issues:
        resolution = by_id[issue["issue_id"]]
        status = resolution.get("status")
        note = str(resolution.get("note") or "").strip()
        if status not in {"resolved", "accepted_uncertain"}:
            raise ValueError(f"La incidencia {issue['issue_id']} continúa pendiente.")
        if not note:
            raise ValueError(f"La incidencia {issue['issue_id']} requiere una nota de resolución.")
        if status == "accepted_uncertain":
            accepted.append({"issue": issue, "review_note": note})
    return accepted


def _parse_reviewed_pages(markdown: str, session_dir: Path, expected_pages: list[int]) -> list[ReviewedPage]:
    matches = list(PAGE_HEADING.finditer(markdown))
    numbers = [int(match.group(1)) for match in matches]
    if numbers != expected_pages:
        raise ValueError(f"Las páginas revisadas deben aparecer una vez y en orden {expected_pages}; recibido {numbers}.")
    images = {int(a): int(b) for a, b in PAGE_IMAGE.findall(markdown)}
    if sorted(images) != expected_pages or any(page != padded for page, padded in images.items()):
        raise ValueError("Cada página debe conservar su enlace ../pages/page_NNN.* con numeración coincidente.")

    result: list[ReviewedPage] = []
    for index, match in enumerate(matches):
        page_number = numbers[index]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        content = markdown[match.end():end].strip()
        image_candidates = sorted((session_dir / "pages").glob(f"page_{page_number:03d}.*"))
        if len(image_candidates) != 1:
            raise ValueError(f"Se esperaba exactamente una imagen fuente para la página {page_number}.")
        sections: list[ReviewedSection] = []
        headings = list(SUBHEADING.finditer(content))
        for sec_index, heading in enumerate(headings):
            sec_end = headings[sec_index + 1].start() if sec_index + 1 < len(headings) else len(content)
            sections.append(ReviewedSection(title=heading.group(1).strip(), content_markdown=content[heading.end():sec_end].strip()))
        result.append(ReviewedPage(
            page_number=page_number,
            source_image=f"../pages/{image_candidates[0].name}",
            content_markdown=content,
            sections=sections,
            spatial_interpretation=(
                match_interpretation.group(1).strip()
                if (match_interpretation := SPATIAL_INTERPRETATION.search(content))
                else None
            ),
        ))
    return result


def publish_cuaderno_review(session_dir: Path | str, reviewer: str, reviewed_at: str | None = None) -> ReviewedCuadernoDocument:
    """Validate and atomically publish a human-reviewed Markdown/JSON pair."""
    session = Path(session_dir).resolve()
    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("El nombre o identificador del revisor no puede estar vacío.")
    machine_json = session / "document.json"
    draft = session / "review" / "transcripcion.md"
    if not machine_json.is_file() or not draft.is_file():
        raise FileNotFoundError("La sesión debe contener document.json y review/transcripcion.md.")
    machine: dict[str, Any] = json.loads(machine_json.read_text(encoding="utf-8"))
    expected_pages = [int(page["page_number"]) for page in machine.get("pages", [])]
    if not expected_pages:
        raise ValueError("document.json no contiene páginas para revisar.")
    markdown = draft.read_text(encoding="utf-8")
    pages = _parse_reviewed_pages(markdown, session, expected_pages)
    accepted_uncertainties = _validated_resolutions(session)

    source_file = str(machine.get("source_file") or "")
    source_path = Path(source_file)
    source_hash = _sha256(source_path) if source_path.is_file() else None
    when = reviewed_at or datetime.now(timezone.utc).isoformat()
    document = ReviewedCuadernoDocument(
        reviewer=reviewer,
        reviewed_at=when,
        source_file=source_file,
        source_sha256=source_hash,
        reviewed_markdown_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        pages=pages,
        accepted_uncertainties=accepted_uncertainties,
        model_guidance=[
            "Usar notas.md y este document.json como fuentes autorizadas de la transcripción.",
            "Consultar ../pages/page_NNN.* para verificar escritura, croquis o contexto visual.",
            "No tratar ../cuaderno_campo.md ni ../document.json como revisados.",
            "No completar ausencias ni resolver incertidumbres sin evidencia visual explícita.",
            "Citar siempre page_number y source_image al afirmar contenido documental.",
        ],
    )

    staging = Path(tempfile.mkdtemp(prefix=".reviewed_", dir=session))
    target = session / "reviewed"
    backup = session / ".reviewed_backup"
    try:
        write_atomic_file(staging / "notas.md", markdown)
        write_atomic_file(staging / "document.json", document.model_dump_json(indent=2) + "\n")
        write_atomic_file(staging / "README.md", REVIEWED_README)
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            target.replace(backup)
        staging.replace(target)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    write_atomic_file(session / "review" / "revision.json", json.dumps({
        "schema_version": 1, "status": "approved", "reviewer": reviewer,
        "reviewed_at": when, "published_markdown": "../reviewed/notas.md",
        "published_document": "../reviewed/document.json",
    }, ensure_ascii=False, indent=2) + "\n")
    return document


REVIEWED_README = """# Contexto para modelos\n\n`notas.md` es la transcripción corregida por una persona y `document.json` es su índice sincronizado. Lea ambos. Use `page_number` y `source_image` para verificar afirmaciones en la página original. Los archivos del directorio padre `cuaderno_campo.md` y `document.json` son resultados automáticos y no sustituyen esta revisión. No complete ausencias ni resuelva escritura dudosa sin evidencia.\n"""


__all__ = ["ReviewedCuadernoDocument", "create_review_draft", "archive_previous_review", "publish_cuaderno_review"]
