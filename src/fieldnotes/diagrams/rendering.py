import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union, Dict, Any

from ..schemas.diagram import DiagramIR
from ..schemas.warnings import ExtractionWarning
from ..vlm.lmstudio import encode_image_to_data_uri
from ..profiles.estadillo import safe_document_stem, write_atomic_file
from .validation import validate_diagram_ir
from .render_mermaid import render_mermaid
from .render_svg import render_svg
from .render_markdown import render_markdown


class DiagramRollbackError(RuntimeError):
    """Excepción lanzada cuando falla la restauración automática del backup tras un error de publicación.

    Conserva la ruta del backup original para permitir la recuperación forense manual
    y encadena la causa raíz del fallo.
    """

    def __init__(self, message: str, backup_dir: Path, original_error: BaseException):
        super().__init__(message)
        self.backup_dir = backup_dir
        self.original_error = original_error


@dataclass(frozen=True)
class DiagramRenderResult:
    """Resultado estructurado e inmutable del renderizado determinista de DiagramIR."""

    diagram_type: str
    mermaid_code: Optional[str]
    svg_content: Optional[str]
    markdown_content: str
    warnings: list[ExtractionWarning]


def render_diagram(
    diagram: DiagramIR,
    asset_relative_path: Optional[str] = None,
) -> DiagramRenderResult:
    """Enruta y renderiza de forma pura y determinista un DiagramIR a sus artefactos visuales y Markdown.

    Reglas de enrutamiento:
    - 'flowchart' -> render_mermaid (Mermaid puro)
    - 'field_sketch' / 'gps_sketch' -> render_svg (SVG XML puro)
    - Todos los tipos -> render_markdown (descripción accesible y autosuficiente)
    """
    if not isinstance(diagram, DiagramIR):
        raise TypeError(f"Se esperaba una instancia de DiagramIR, recibido: {type(diagram).__name__}")

    validate_diagram_ir(diagram, raise_on_error=True)

    if diagram.diagram_type == "flowchart":
        mermaid_code = render_mermaid(diagram)
        svg_content = None
        md_content = render_markdown(diagram, asset_relative_path=asset_relative_path)
    elif diagram.diagram_type in ("field_sketch", "gps_sketch"):
        mermaid_code = None
        svg_content = render_svg(diagram)
        md_content = render_markdown(diagram, asset_relative_path=asset_relative_path)
    else:
        raise ValueError(f"diagram_type no soportado para renderizado: '{diagram.diagram_type}'")

    return DiagramRenderResult(
        diagram_type=diagram.diagram_type,
        mermaid_code=mermaid_code,
        svg_content=svg_content,
        markdown_content=md_content,
        warnings=list(diagram.warnings),
    )


def publish_rendered_diagram(
    diagram: DiagramIR,
    source_image_path: Union[str, Path],
    output_base_dir: Union[str, Path],
    document_name: Optional[str] = None,
) -> dict[str, Path]:
    """Publica de forma atómica, confinada y reversible los artefactos canónicos y derivados de DiagramIR.

    Garantías de seguridad y robustez:
    1. Preservación canónica: Preserva 'diagram.json' y la imagen original byte a byte en assets/.
    2. Derivados de renderizado:
       - flowchart -> 'diagram.mmd' y 'diagram.md'
       - field_sketch / gps_sketch -> 'assets/<stem>.svg' y 'diagram.md'
    3. Confinamiento estricto: Valida rutas contra Directory Traversal.
    4. Staging aislado: Toda escritura ocurre en .staging_diagram_render_<stem>_<uuid>.
    5. Atomicidad y Rollback: En caso de cualquier error, se limpia staging y se restaura el backup previo.
       Si la restauración del backup falla, el backup NUNCA se elimina y se lanza DiagramRollbackError.
    """
    in_img = Path(source_image_path).resolve()
    if not in_img.is_file():
        raise FileNotFoundError(f"Imagen original no encontrada: {in_img}")

    # Validar imagen
    encode_image_to_data_uri(in_img)

    # Validar diagrama antes de cualquier operación de I/O
    validate_diagram_ir(diagram, raise_on_error=True)

    base_out = Path(output_base_dir).resolve()
    base_out.mkdir(parents=True, exist_ok=True)

    # Sanitizar y validar document_name
    if document_name is not None:
        if not isinstance(document_name, str) or not document_name.strip():
            raise ValueError("document_name no puede estar vacío.")
        raw_doc_name = document_name.strip()
        if "/" in raw_doc_name or "\\" in raw_doc_name or ".." in raw_doc_name:
            raise ValueError(
                f"document_name '{document_name}' contiene separadores de ruta o secuencias de escape no permitidas."
            )
        stem = safe_document_stem(raw_doc_name)
    else:
        stem = safe_document_stem(in_img)

    canonical_dir = (base_out / stem).resolve()

    try:
        canonical_dir.relative_to(base_out)
    except ValueError as exc:
        raise ValueError(
            f"Ruta canónica '{canonical_dir}' escapa del directorio base permitido '{base_out}'"
        ) from exc

    # Renderizar derivados
    svg_rel_path = f"assets/{stem}.svg" if diagram.diagram_type != "flowchart" else None
    render_result = render_diagram(diagram, asset_relative_path=svg_rel_path)

    staging_dir = (base_out / f".staging_diagram_render_{stem}_{uuid.uuid4().hex}").resolve()
    staging_dir.relative_to(base_out)
    backup_dir: Optional[Path] = None

    try:
        staging_assets_dir = staging_dir / "assets"
        staging_assets_dir.mkdir(parents=True, exist_ok=True)

        # 1. Copia exacta de la imagen original en assets/
        img_ext = in_img.suffix.lower() if in_img.suffix else ".png"
        target_img_name = f"{stem}_original{img_ext}"
        staging_img_path = staging_assets_dir / target_img_name
        shutil.copy2(in_img, staging_img_path)

        # 2. Escritura canónica de diagram.json
        staging_json_path = staging_dir / "diagram.json"
        json_content = diagram.model_dump_json(indent=2)
        write_atomic_file(staging_json_path, json_content)

        # 3. Escritura de Markdown accesible
        staging_md_path = staging_dir / "diagram.md"
        write_atomic_file(staging_md_path, render_result.markdown_content)

        # 4. Escritura de artefacto gráfico según tipo
        staging_mmd_path: Optional[Path] = None
        staging_svg_path: Optional[Path] = None

        if diagram.diagram_type == "flowchart":
            staging_mmd_path = staging_dir / "diagram.mmd"
            write_atomic_file(staging_mmd_path, render_result.mermaid_code or "")
        else:
            staging_svg_path = staging_assets_dir / f"{stem}.svg"
            write_atomic_file(staging_svg_path, render_result.svg_content or "")

        # 5. Publicación atómica con respaldo
        if canonical_dir.exists():
            backup_dir = (base_out / f".backup_diagram_render_{stem}_{uuid.uuid4().hex}").resolve()
            backup_dir.relative_to(base_out)
            shutil.move(str(canonical_dir), str(backup_dir))

        # 6. Mover staging a canonical
        try:
            shutil.move(str(staging_dir), str(canonical_dir))
        except Exception as move_exc:
            # Si quedó un canonical_dir parcial tras el fallo de move, limpiarlo
            if canonical_dir.exists():
                try:
                    canonical_dir.relative_to(base_out)
                    shutil.rmtree(str(canonical_dir), ignore_errors=True)
                except ValueError:
                    pass

            # Restaurar backup si existía
            if backup_dir and backup_dir.exists():
                try:
                    backup_dir.relative_to(base_out)
                    shutil.move(str(backup_dir), str(canonical_dir))
                except Exception as rollback_exc:
                    # NUNCA borrar el backup si la restauración falla
                    raise DiagramRollbackError(
                        f"Fallo crítico durante el swap de publicación a '{canonical_dir}' "
                        f"y la restauración automática del backup en '{backup_dir}' falló. "
                        f"El backup ha sido conservado intacto para recuperación manual.",
                        backup_dir=backup_dir,
                        original_error=move_exc,
                    ) from rollback_exc

            raise move_exc

        # 7. Éxito: limpiar backup si existió
        if backup_dir and backup_dir.exists():
            shutil.rmtree(str(backup_dir), ignore_errors=True)

        published_json = canonical_dir / "diagram.json"
        published_img = canonical_dir / "assets" / target_img_name
        published_md = canonical_dir / "diagram.md"
        published_mmd = (canonical_dir / "diagram.mmd") if diagram.diagram_type == "flowchart" else None
        published_svg = (canonical_dir / "assets" / f"{stem}.svg") if diagram.diagram_type != "flowchart" else None
        published_artifact = published_mmd if published_mmd is not None else published_svg

        return {
            "canonical_dir": canonical_dir,
            "diagram_json": published_json,
            "original_image": published_img,
            "markdown": published_md,
            "mermaid_file": published_mmd,
            "svg_file": published_svg,
            "rendered_artifact": published_artifact,
        }

    except DiagramRollbackError:
        # Si ya se lanzó DiagramRollbackError, limpiar staging y re-lanzar sin tocar el backup
        if staging_dir.exists():
            try:
                staging_dir.relative_to(base_out)
                shutil.rmtree(str(staging_dir), ignore_errors=True)
            except ValueError:
                pass
        raise

    except Exception as exc:
        # Fallo ocurrido durante el staging antes de realizar el swap o en otra etapa
        if staging_dir.exists():
            try:
                staging_dir.relative_to(base_out)
                shutil.rmtree(str(staging_dir), ignore_errors=True)
            except ValueError:
                pass

        if backup_dir and backup_dir.exists():
            try:
                backup_dir.relative_to(base_out)
                if canonical_dir.exists():
                    canonical_dir.relative_to(base_out)
                    shutil.rmtree(str(canonical_dir), ignore_errors=True)
                shutil.move(str(backup_dir), str(canonical_dir))
            except Exception as rollback_exc:
                raise DiagramRollbackError(
                    f"Fallo durante la publicación en '{canonical_dir}' "
                    f"y la restauración del backup en '{backup_dir}' falló. "
                    f"El backup ha sido conservado intacto.",
                    backup_dir=backup_dir,
                    original_error=exc,
                ) from rollback_exc
        raise