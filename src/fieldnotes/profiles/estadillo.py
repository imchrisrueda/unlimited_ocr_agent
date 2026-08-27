import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional, Union, Any, Tuple

from src.fieldnotes.schemas.estadillo import EstadilloPage, EstadilloDocument
from src.fieldnotes.schemas.dto import EstadilloPageDTO, dto_to_estadillo_page
from src.fieldnotes.schemas.review import review_issues_to_json
from src.fieldnotes.review.issues import (
    extract_review_issues_from_document,
    extract_invalid_region_issues,
    deduplicate_and_sort_issues,
    collect_dto_regions,
)
from src.fieldnotes.review.crops import generate_review_crops_for_issues
from src.fieldnotes.merge.estadillo import merge_estadillo_pages
from src.fieldnotes.normalization.estadillo import normalize_species
from src.fieldnotes.validation.estadillo import validate_estadillo_document
from src.fieldnotes.render.estadillo_delivery import (
    render_estadillo_csv,
    render_estadillo_notes,
    resolve_session_date,
)
from src.fieldnotes.schemas.warnings import ExtractionWarning
from src.fieldnotes.vlm.errors import LMStudioEmptyResponseError, LMStudioResponseTruncatedError

ESTADILLO_PROMPT_TEMPLATE = """Extrae de forma rigurosa y exhaustiva todos los datos de esta página de notas de campo (estadillo forestal/agronómico).

INSTRUCCIONES DE EXTRACCIÓN:
1. Fuente primaria: La imagen es la fuente primaria de evidencia visual y de verdad; el texto OCR suministrado es una hipótesis auxiliar de apoyo para confirmar textos y números.
2. Cabecera (objeto 'header'):
   - Si la página contiene metadatos en la parte superior, extráelos en 'header':
     * 'objetivo': Texto tras 'Objetivo:' o similar
     * 'fecha': Fecha tras 'Fecha:' o similar
     * 'asistentes': Nombres/iniciales tras 'Asistentes:' o similar
     * 'equipamiento': Texto de equipamiento o null si vacío
     * 'situacion_atmosferica': Situación meteorológica o null si vacío
     * 'especies_declaradas': Texto tras '*Especies:' o similar
   - Si la página no contiene cabecera, asigna header=null.
3. Filas de datos (lista 'rows'):
   - Extrae TODAS las filas de la tabla de datos de la página en la lista 'rows' (sin omitir ninguna fila visible ni devolver la lista vacía si hay datos).
   - Campos de cada fila:
     * 'id': Identificador o null si no está presente
     * 'col': Número de columna (p. ej. 1). Si se indica al inicio y se deja en blanco en las siguientes filas de la misma columna, extrae 1 en la primera fila y null en las filas donde no esté explícitamente reescrita.
     * 'fil': Número de fila (p. ej. 26, 25, 24...)
     * 'especie': Código de especie (p. ej. "Ah", "Ap", "Ar", "Mz")
     * 'altura_cm': Altura (número decimal o entero, p. ej. 5.5, 12, o null si está en blanco)
     * 'foto': Número de foto (p. ej. "43") o null si está en blanco
     * 'bbch': Estado fenológico BBCH (p. ej. "22", "18", "25.2.5")
     * 'observaciones': Texto de observaciones o null si está en blanco
     * 'uncertain_fields': Lista de nombres de campos con caracteres dudosos/ilegibles (p. ej. ["altura_cm"]), o lista vacía []
     * 'bbox': Bounding box OBLIGATORIO para CADA fila extraída relativo a las dimensiones TOTALES de la imagen completa. Usa ENTEROS en cuadrícula 0..1000 donde 0=borde superior/izquierdo y 1000=borde inferior/derecho de la imagen completa. Fórmula: x0=round(píxel_izquierdo*1000/ancho_imagen), y0=round(píxel_superior*1000/alto_imagen), x1=round(píxel_derecho*1000/ancho_imagen), y1=round(píxel_inferior*1000/alto_imagen). Todos los valores son ENTEROS en [0, 1000]. NUNCA generar números decimales, negativos ni coordenadas mayores a 1000. Ejemplo para página con cabecera y 20 filas: fila1={{x0:30,y0:220,x1:970,y1:270}}, fila10={{x0:30,y0:600,x1:970,y1:650}}, fila20={{x0:30,y0:940,x1:970,y1:990}}.
     * 'field_bboxes': Mapa opcional {{campo: {{x0, y0, x1, y1}}}} con las mismas coordenadas enteras 0..1000 ÚNICAMENTE si una celda concreta es dudosa y requiere recorte específico; en caso contrario null.
4. No inventes datos. Si un campo no es visible o está ausente, usa null.
5. Si hay notas o anotaciones fuera de las tablas principales, captúralas en 'additional_text'; en caso contrario usa null.
6. Si detectas regiones dudosas adicionales que requieran verificación humana, puedes incluir objetos en 'regions' con {{field, row_key, bbox: {{x0, y0, x1, y1}}}} con las mismas coordenadas enteras 0..1000.
7. Devuelve exclusivamente el objeto JSON 'EstadilloPageDTO' con page_number={page_number}, sin Markdown ni bloques de razonamiento interno.
"""


ESTADILLO_DEFAULT_EXTRA_BODY: dict[str, Any] = {
    "chat_template_kwargs": {"enable_thinking": False},
    "enable_thinking": False,
    "reasoning_effort": "none",
}

def build_estadillo_page_prompt(page_number: int, user_prompt: Optional[str] = None) -> str:
    """Conserva el contrato estructurado y añade instrucciones del usuario como contexto."""
    result = ESTADILLO_PROMPT_TEMPLATE.format(page_number=page_number)
    if user_prompt and user_prompt.strip():
        result += (
            "\n\n--- INSTRUCCIÓN ADICIONAL DEL USUARIO ---\n"
            f"{user_prompt.strip()}\n"
            "--- FIN INSTRUCCIÓN ADICIONAL ---\n"
        )
    return result

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def safe_document_stem(file_path: Union[str, Path]) -> str:
    """Genera un nombre de directorio determinista, seguro y acotado para Windows y POSIX."""
    p = Path(file_path)
    raw_stem = p.stem.strip()

    # Reemplazar caracteres no permitidos en sistemas de archivos
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_stem)
    # Reemplazar secuencias de espacios en blanco y puntos/guiones bajos extremos
    sanitized = re.sub(r"\s+", "_", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized).strip(". _")

    if not sanitized or sanitized in {".", ".."}:
        sanitized = "document"

    # Manejar nombres de dispositivos reservados en Windows
    if sanitized.upper() in _WINDOWS_RESERVED_NAMES:
        sanitized = f"doc_{sanitized}"

    # Limitar longitud máxima a 100 caracteres preservando validez
    if len(sanitized) > 100:
        sanitized = sanitized[:100].rstrip(". _")
        if not sanitized:
            sanitized = "document"

    return sanitized


def write_atomic_file(target_path: Path, content: str) -> None:
    """Escribe un archivo de texto de forma atómica cerrando descriptores y limpiando en caso de error."""
    target_path = target_path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = target_path.parent / f".tmp_{target_path.name}_{uuid.uuid4().hex}"
    fd = None
    try:
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with open(fd, "w", encoding="utf-8", newline="\n", closefd=True) as f:
            fd = None  # python open maneja el cierre del descriptor
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(temp_path, target_path)
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


class EstadilloProfile:
    """Perfil de dominio para procesamiento estructurado de estadillos de campo."""

    def __init__(
        self,
        agent: Any,
        output_base_dir: Optional[Union[str, Path]] = None,
        vision_model: Optional[str] = None,
    ):
        self.agent = agent
        self.output_base_dir = (
            Path(output_base_dir).resolve()
            if output_base_dir is not None
            else Path(agent.base_output_dir).resolve()
        )
        if vision_model:
            self.agent.vision_model = vision_model

    def run(
        self,
        file_path: Union[str, Path],
        prompt: Optional[str] = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Tuple[str, EstadilloDocument]:
        """Ejecuta el pipeline completo de estadillo sobre un archivo PDF o imagen.

        Flujo:
        1. Inferencia OCR y rasterizado en worker aislado.
        2. Creación de directorio de staging aislado bajo output_base_dir.
        3. Extracción visual estructurada compacta (EstadilloPageDTO) con VLM.
        4. Conversión pura y determinista a modelo de dominio canónico (EstadilloPage).
        5. Fusión pura de páginas y reconciliación de cabecera.
        6. Normalización auditable de especies.
        7. Validación determinista de calidad.
        8. Renderizado determinista de notas.md y datos.csv vinculados.
        9. Generación de ReviewIssues y crops visuales confinados en review/.
        10. Publicación atómica del directorio de staging a la ruta canónica con rollback.
        """
        input_path = Path(file_path).resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Archivo de entrada no encontrado: {input_path}")

        stem = safe_document_stem(input_path)
        canonical_dir = (self.output_base_dir / stem).resolve()

        # Verificar confinamiento de ruta canónica
        try:
            canonical_dir.relative_to(self.output_base_dir)
        except ValueError as exc:
            raise ValueError(
                f"Ruta canónica '{canonical_dir}' escapa del directorio base '{self.output_base_dir}'"
            ) from exc

        # Directorio de staging aislado y confinado
        staging_dir = (self.output_base_dir / f".staging_{stem}_{uuid.uuid4().hex}").resolve()
        staging_dir.relative_to(self.output_base_dir)

        backup_dir: Optional[Path] = None

        try:
            # 1. OCR Phase (Worker ejecuta en subproceso y libera VRAM)
            is_pdf = input_path.suffix.lower() == ".pdf"
            if is_pdf:
                self.agent.extract_from_pdf(str(input_path))
            else:
                self.agent.extract_from_image(str(input_path))

            artifacts = self.agent.page_artifacts
            if not artifacts:
                raise RuntimeError(f"No se generaron artefactos de página para '{input_path}'")

            # Preparar layout en staging
            staging_pages_dir = staging_dir / "pages"
            staging_raw_dir = staging_dir / "raw"
            staging_assets_dir = staging_dir / "assets"
            staging_review_dir = staging_dir / "review"

            staging_pages_dir.mkdir(parents=True, exist_ok=True)
            staging_raw_dir.mkdir(parents=True, exist_ok=True)
            staging_assets_dir.mkdir(parents=True, exist_ok=True)
            staging_review_dir.mkdir(parents=True, exist_ok=True)

            # Validar y copiar artefactos a staging
            for art in artifacts:
                # Normalizar nombre de imagen de página a page_001.png
                img_ext = art.image_path.suffix.lower() if art.image_path else ".png"
                if not img_ext:
                    img_ext = ".png"
                page_img_name = f"page_{art.page_number:03d}{img_ext}"
                target_img = staging_pages_dir / page_img_name

                if art.image_path and art.image_path.is_file():
                    shutil.copy2(art.image_path, target_img)
                else:
                    raise FileNotFoundError(f"Artefacto de imagen no encontrado: {art.image_path}")

                target_raw = staging_raw_dir / f"page_{art.page_number:03d}.md"
                raw_src = Path(self.agent.output_dir) / "raw" / f"page_{art.page_number:03d}.md"
                if raw_src.is_file():
                    shutil.copy2(raw_src, target_raw)
                elif art.raw_ocr is not None:
                    target_raw.write_text(art.raw_ocr, encoding="utf-8")

            # 2. Sequential Compact VLM Extraction & Pure Canonical Conversion
            extracted_pages: list[EstadilloPage] = []
            page_dtos: list[EstadilloPageDTO] = []

            for art in artifacts:
                page_prompt = build_estadillo_page_prompt(art.page_number, prompt)

                call_kwargs = dict(kwargs)
                call_kwargs.setdefault("reasoning_effort", "none")

                # Combinación segura de extra_body con defaults de thinking deshabilitado sin mutar el dict original del usuario
                user_extra_body = kwargs.get("extra_body")
                effective_extra_body = dict(ESTADILLO_DEFAULT_EXTRA_BODY)
                if user_extra_body is not None:
                    effective_extra_body.update(user_extra_body)
                    if "chat_template_kwargs" in user_extra_body and isinstance(user_extra_body["chat_template_kwargs"], dict):
                        merged_chat_kwargs = dict(ESTADILLO_DEFAULT_EXTRA_BODY["chat_template_kwargs"])
                        merged_chat_kwargs.update(user_extra_body["chat_template_kwargs"])
                        effective_extra_body["chat_template_kwargs"] = merged_chat_kwargs
                call_kwargs["extra_body"] = effective_extra_body

                # Inferencia estructurada compacta con EstadilloPageDTO
                page_dto: EstadilloPageDTO = self.agent.ask_page_vision_structured(
                    page_artifact=art,
                    prompt=page_prompt,
                    schema=EstadilloPageDTO,
                    max_tokens=max_tokens,
                    **call_kwargs,
                )

                if isinstance(page_dto, EstadilloPageDTO):
                    page_dtos.append(page_dto)

                # Conversión determinista y pura al modelo canónico de dominio EstadilloPage
                page_data = dto_to_estadillo_page(page_dto, page_number=art.page_number)
                extracted_pages.append(page_data)

            # 3. Pure Merge & Header Reconciliation
            merged_doc = merge_estadillo_pages(extracted_pages, source_file=str(input_path))

            # 4. Pure Species Normalization
            norm_doc = normalize_species(merged_doc)

            # 5. Pure Deterministic Validation
            validated_doc = validate_estadillo_document(norm_doc)

            # 6. Resolver la sesión sin inventar fechas y renderizar la entrega canónica.
            session_date = resolve_session_date(validated_doc)
            if session_date is None:
                validated_doc = validated_doc.model_copy(deep=True)
                validated_doc.warnings.append(
                    ExtractionWarning(
                        code="SESSION_DATE_UNRESOLVED",
                        message=(
                            "La fecha de sesión no existe, no es ISO válida o presenta conflicto; "
                            f"se conserva el nombre seguro del documento '{stem}'."
                        ),
                        severity="warning",
                        source_page=(
                            validated_doc.header.fecha.source_page
                            if validated_doc.header and validated_doc.header.fecha
                            else None
                        ),
                        field_name="fecha",
                    )
                )
            session_name = session_date or stem
            canonical_dir = (self.output_base_dir / session_name).resolve()
            try:
                canonical_dir.relative_to(self.output_base_dir)
            except ValueError as exc:
                raise ValueError(
                    f"Ruta canónica '{canonical_dir}' escapa del directorio base '{self.output_base_dir}'"
                ) from exc

            notes_output = render_estadillo_notes(validated_doc)
            csv_output = render_estadillo_csv(validated_doc)

            # 7. Generación de ReviewIssues y Crops visuales confinados
            review_issues = extract_review_issues_from_document(validated_doc)
            invalid_region_issues = extract_invalid_region_issues(page_dtos)
            if invalid_region_issues:
                review_issues = deduplicate_and_sort_issues(review_issues + invalid_region_issues)
            dto_regions = collect_dto_regions(page_dtos)

            page_images = {
                art.page_number: staging_pages_dir / f"page_{art.page_number:03d}{art.image_path.suffix.lower() if art.image_path else '.png'}"
                for art in artifacts
            }

            review_issues = generate_review_crops_for_issues(
                issues=review_issues,
                page_images=page_images,
                regions_map=dto_regions,
                review_dir=staging_review_dir,
                canonical_base_dir=staging_dir,
            )

            # 8. Escribir archivos finales en staging
            notes_path = staging_dir / "notas.md"
            csv_path = staging_dir / "datos.csv"
            document_json_path = staging_dir / "document.json"
            issues_json_path = staging_review_dir / "issues.json"

            write_atomic_file(notes_path, notes_output)
            write_atomic_file(csv_path, csv_output)
            write_atomic_file(document_json_path, validated_doc.model_dump_json(indent=2))
            write_atomic_file(issues_json_path, review_issues_to_json(review_issues))

            # 9. Publicación atómica de staging a canonical_dir con rollback
            if canonical_dir.exists():
                backup_dir = (self.output_base_dir / f".backup_{stem}_{uuid.uuid4().hex}").resolve()
                shutil.move(str(canonical_dir), str(backup_dir))

            try:
                shutil.move(str(staging_dir), str(canonical_dir))
                if backup_dir and backup_dir.exists():
                    shutil.rmtree(str(backup_dir), ignore_errors=True)
            except Exception:
                # Rollback si falla el renombrado de staging a canonical
                if backup_dir and backup_dir.exists() and not canonical_dir.exists():
                    shutil.move(str(backup_dir), str(canonical_dir))
                raise

            return notes_output, validated_doc

        except Exception:
            # En caso de fallo, limpiar exclusivamente staging y no tocar canonical preexistente
            if staging_dir.exists():
                shutil.rmtree(str(staging_dir), ignore_errors=True)
            if backup_dir and backup_dir.exists():
                if not canonical_dir.exists():
                    shutil.move(str(backup_dir), str(canonical_dir))
                else:
                    shutil.rmtree(str(backup_dir), ignore_errors=True)
            raise
