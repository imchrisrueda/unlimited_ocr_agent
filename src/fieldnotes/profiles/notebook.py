import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional, Union, Any, Tuple, Dict, List, Literal
from pydantic import BaseModel, ConfigDict, Field

from ..schemas.notebook import (
    NotebookConfig,
    NotebookPageDTO,
    NotebookPage,
    NotebookDocument,
    NotebookSectionDTO,
    NotebookNoteItemDTO,
    GenericTableDTO,
    dto_to_notebook_page,
)
from ..schemas.diagram import DiagramDTO, dto_to_diagram_ir
from ..schemas.dto import EstadilloHeaderDTO, VisualRegion1000DTO
from ..schemas.review import review_issues_to_json, ReviewIssue
from ..review.issues import (
    extract_review_issues_from_document,
    extract_invalid_region_issues,
    deduplicate_and_sort_issues,
    collect_dto_regions,
)
from ..review.crops import generate_review_crops_for_issues
from ..merge.notebook import merge_notebook_pages
from ..validation.notebook import validate_notebook_document
from ..render.notebook import render_notebook_markdown
from ..diagrams.render_svg import render_svg
from ..diagrams.render_mermaid import render_mermaid
from ..vlm.errors import StructuredOutputValidationError
from .estadillo import safe_document_stem, write_atomic_file


class NotebookRollbackError(RuntimeError):
    """Excepción lanzada cuando falla la restauración automática del backup tras un error de publicación.

    Conserva la ruta del backup original para permitir la recuperación forense manual
    y encadena la causa raíz del fallo.
    """

    def __init__(self, message: str, backup_dir: Path, original_error: BaseException):
        super().__init__(message)
        self.backup_dir = backup_dir
        self.original_error = original_error


NOTEBOOK_PROMPT_TEMPLATE = """Extrae de forma exhaustiva, rigurosa y fiel a la evidencia todo el contenido de esta página de cuaderno de notas de campo.

INSTRUCCIONES DE EXTRACCIÓN:
1. Fuente primaria de verdad: La imagen es la fuente primaria de evidencia visual. El texto OCR es una hipótesis de apoyo.
2. Cabecera y metadatos (objeto 'header'):
   - Si la página contiene metadatos globales al inicio (Objetivo, Fecha, Asistentes, Equipamiento, Situación atmosférica, Especies declaradas), extráelos en 'header'.
   - Si no contiene cabecera, asigna header=null.
3. Filas tabulares de estadillo forestal/agronómico (lista 'estadillo_rows'):
   - Si la página contiene una tabla de registros con columnas como id, col, fil, especie, altura_cm, foto, bbch, observaciones, extrae cada fila en 'estadillo_rows'.
   - Si no hay tabla de estadillo, deja 'estadillo_rows' como lista vacía [].
4. Secciones de texto libre (lista 'sections'):
   - Extrae cualquier título, encabezado, párrafo, resumen o descripción en 'sections' con {{title, content, paragraphs: [...], level: 2}}.
   - No fabriques secciones ausentes.
5. Notas y listas breves (lista 'notes'):
   - Extrae anotaciones marginales, listas de puntos o advertencias en 'notes' con {{text, category}}.
6. Tablas genéricas (lista 'tables'):
   - Si hay tablas no tabulares de estadillo (inventarios, mediciones meteorológicas, conteos heterogéneos), extráelas en 'tables' con {{title, headers: [...], rows: [[...]], caption}}.
7. Diagramas, croquis o diagramas de flujo (lista 'diagrams'):
   - Si la página contiene un croquis de campo ('field_sketch'), diagrama de flujo ('flowchart') o esquema GPS ('gps_sketch'), extráelo en 'diagrams' como estructura semántica DiagramDTO con: diagram_type, title, description, orientation, georeferenced, crs, geographic_coordinates, areas, points, lines, labels, relations.
   - NUNCA generes código Mermaid, SVG ni Markdown en la respuesta JSON; extrae exclusivamente la representación estructurada con coordenadas relativas [0.0, 1.0].
8. Texto adicional fuera de bloques (campo 'additional_text'):
   - Cualquier texto legible no clasificable en los bloques anteriores, captúralo en 'additional_text'.
9. Incertidumbres y regiones de revisión (lista 'regions'):
   - Si una lectura es dudosa, marca uncertain=true e incluye coordenadas en 'regions' con {{field, row_key, bbox: {{x0, y0, x1, y1}}}} en cuadrícula entera 0..1000.
10. Devuelve exclusivamente el objeto JSON 'NotebookPageDTO' con page_number={page_number}, sin prosa introductoria ni bloques de razonamiento.
"""

NOTEBOOK_DEFAULT_EXTRA_BODY: dict[str, Any] = {
    "chat_template_kwargs": {"enable_thinking": False},
    "enable_thinking": False,
    "reasoning_effort": "none",
}


class CuadernoTextPageDTO(BaseModel):
    """Contenido textual de página; excluye por contrato estadillo y geometría."""

    schema_version: Literal[1] = 1
    page_number: int = Field(..., ge=1)
    header: Optional[EstadilloHeaderDTO] = None
    sections: list[NotebookSectionDTO] = Field(default_factory=list)
    notes: list[NotebookNoteItemDTO] = Field(default_factory=list)
    tables: list[GenericTableDTO] = Field(default_factory=list)
    additional_text: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    regions: list[VisualRegion1000DTO] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", strict=True)


class VisualItemDTO(BaseModel):
    """Inventario ligero previo a la extracción geométrica."""

    visual_type: Literal["field_sketch", "flowchart", "gps_sketch"]
    description: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid", strict=True)


class VisualInventoryDTO(BaseModel):
    items: list[VisualItemDTO] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", strict=True)


VISUAL_INVENTORY_PROMPT = """Inspecciona exclusivamente la evidencia visual de esta página.
Enumera ÚNICAMENTE croquis de campo reales (mapas de parcelas, dibujos del terreno), esquemas GPS o diagramas de flujo de procesos.
REGLAS ESTRICTAS DE EXCLUSIÓN:
- NUNCA clasifiques como croquis o diagrama fragmentos de texto normal encerrados en cajas, recuadros o marcos (por ejemplo: fechas enmarcadas como [09/06] OK, títulos enmarcados, tarjetas de notas o llamadas).
- NUNCA clasifiques como diagrama una lista de tareas, notas con viñetas o texto con flechas simples indicativas.
- No confundas una tabla ni bloques de texto con una figura.
- No inventes elementos. Si no hay diagramas o croquis reales inequívocos, devuelve items=[].
Clasifica cada elemento válido como field_sketch, flowchart o gps_sketch y descríbelo brevemente."""


def build_notebook_page_prompt(
    page_number: int,
    config: Optional[NotebookConfig] = None,
    user_prompt: Optional[str] = None,
) -> str:
    """Construye el prompt contractual inmutable enriquecido con guías de configuración e instrucciones de usuario."""
    base_prompt = NOTEBOOK_PROMPT_TEMPLATE.format(page_number=page_number)

    if config:
        if not config.is_diagram_extraction_enabled(page_number):
            base_prompt += (
                "\n\nDIRECTIVA DE DIAGRAMAS: La extracción de diagramas/croquis está DESACTIVADA para esta página. "
                "Devuelve siempre diagrams=[]. Transcribe todo el texto legible (incluyendo notas enmarcadas o en cajas) "
                "en sections, notes o tables según corresponda, de forma limpia y ordenada.\n"
            )
        guidance = config.get_prompt_guidance()
        if guidance:
            base_prompt += f"\n\nGUÍAS ESPECÍFICAS DE CONFIGURACIÓN:\n{guidance}\n"

    if user_prompt and user_prompt.strip():
        base_prompt += (
            f"\n\n--- INSTRUCCIÓN ADICIONAL DEL USUARIO ---\n"
            f"{user_prompt.strip()}\n"
            f"--- FIN INSTRUCCIÓN ADICIONAL ---\n"
        )

    return base_prompt


class NotebookProfile:
    """Perfil general de cuaderno para documentos mixtos, textuales, tabulares y con diagramas."""

    document_filename = "notebook.md"
    staging_prefix = "notebook"
    page_schema = NotebookPageDTO

    def __init__(
        self,
        agent: Any,
        output_base_dir: Optional[Union[str, Path]] = None,
        vision_model: Optional[str] = None,
        config: Optional[NotebookConfig] = None,
    ):
        self.agent = agent
        self.output_base_dir = (
            Path(output_base_dir).resolve()
            if output_base_dir is not None
            else Path(agent.base_output_dir).resolve()
        )
        if vision_model:
            self.agent.vision_model = vision_model
        self.config = config or NotebookConfig()

    def render_document(self, doc: NotebookDocument, assets_map: dict[str, str]) -> str:
        return render_notebook_markdown(doc=doc, config=self.config, assets_map=assets_map)

    def build_page_prompt(self, page_number: int, prompt: Optional[str]) -> str:
        return build_notebook_page_prompt(page_number=page_number, config=self.config, user_prompt=prompt)

    def normalize_page_dto(self, value: Any) -> NotebookPageDTO:
        if isinstance(value, NotebookPageDTO):
            dto = value
        else:
            dto = NotebookPageDTO(**value.model_dump(exclude={"schema_version"}), estadillo_rows=[], diagrams=[])
        if not self.config.is_diagram_extraction_enabled(dto.page_number):
            dto = dto.model_copy(update={"diagrams": []})
        return dto

    def enrich_page_dto(self, art: Any, page_dto: NotebookPageDTO, max_tokens: int, call_kwargs: dict[str, Any]) -> NotebookPageDTO:
        return page_dto

    def run(
        self,
        file_path: Union[str, Path],
        prompt: Optional[str] = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Tuple[str, NotebookDocument]:
        """Ejecuta el pipeline completo del perfil notebook sobre un PDF o imagen.

        Flujo:
        1. Inferencia OCR y rasterizado en worker aislado.
        2. Directorio de staging aislado y confinado.
        3. Extracción visual estructurada compacta (NotebookPageDTO) con VLM.
        4. Conversión determinista a modelo canónico (NotebookPage).
        5. Fusión pura de páginas y reconciliación de cabecera.
        6. Validación y normalización no destructiva.
        7. Renderizado y derivación de diagramas (SVG / Mermaid).
        8. Renderizado Markdown determinista de dos tablas obligatorias de AGENTS.md + secciones flexibles.
        9. Generación de ReviewIssues y crops visuales confinados en review/.
        10. Publicación atómica de staging a canonical_dir con rollback y preservación de backup ante error.
        """
        progress = kwargs.get("progress")

        input_path = Path(file_path).resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Archivo de entrada no encontrado: {input_path}")

        # Soporte para carpetas de imágenes: convertir a PDF primero
        if input_path.is_dir():
            from ..ingest.images import prepare_input_source
            if progress:
                progress.set_phase(1, "Preparando entrada", f"Convirtiendo carpeta '{input_path.name}' a PDF...")
            work_dir = Path(self.agent.output_dir) if hasattr(self.agent, "output_dir") else None
            input_path, _, _ = prepare_input_source(input_path, working_dir=work_dir)
        elif progress:
            progress.set_phase(1, "Preparando entrada", f"Documento: {input_path.name}")

        stem = safe_document_stem(input_path)
        canonical_dir = (self.output_base_dir / stem).resolve()

        # Verificar confinamiento de ruta canónica contra Directory Traversal
        try:
            canonical_dir.relative_to(self.output_base_dir)
        except ValueError as exc:
            raise ValueError(
                f"Ruta canónica '{canonical_dir}' escapa del directorio base '{self.output_base_dir}'"
            ) from exc

        # Directorio de staging aislado y confinado
        staging_dir = (self.output_base_dir / f".staging_{self.staging_prefix}_{stem}_{uuid.uuid4().hex}").resolve()
        staging_dir.relative_to(self.output_base_dir)

        backup_dir: Optional[Path] = None

        try:
            # 1. Fase OCR (Worker aislado libera VRAM)
            is_pdf = input_path.suffix.lower() == ".pdf"
            if progress:
                progress.set_phase(2, "Ingesta y rasterizado", "Extrayendo páginas del PDF..." if is_pdf else "Cargando imagen...")
            if progress:
                progress.set_phase(3, "Inferencia OCR", "Ejecutando Unlimited-OCR en worker aislado...")
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

            # 2. Extracción secuencial estructurada compacta con VLM
            if progress:
                progress.set_phase(4, "Extracción VLM multimodal", f"0/{len(artifacts)} páginas analizadas")

            extracted_pages: list[NotebookPage] = []
            page_dtos: list[NotebookPageDTO] = []

            for idx, art in enumerate(artifacts):
                if progress:
                    progress.update_substep(f"Página {art.page_number}/{len(artifacts)}")
                page_prompt = self.build_page_prompt(art.page_number, prompt)

                call_kwargs = dict(kwargs)
                call_kwargs.pop("progress", None)
                call_kwargs.setdefault("reasoning_effort", "none")

                user_extra_body = kwargs.get("extra_body")
                effective_extra_body = dict(NOTEBOOK_DEFAULT_EXTRA_BODY)
                if user_extra_body is not None:
                    effective_extra_body.update(user_extra_body)
                    if "chat_template_kwargs" in user_extra_body and isinstance(user_extra_body["chat_template_kwargs"], dict):
                        merged_chat_kwargs = dict(NOTEBOOK_DEFAULT_EXTRA_BODY["chat_template_kwargs"])
                        merged_chat_kwargs.update(user_extra_body["chat_template_kwargs"])
                        effective_extra_body["chat_template_kwargs"] = merged_chat_kwargs
                call_kwargs["extra_body"] = effective_extra_body

                # Inferencia estructurada compacta con NotebookPageDTO
                try:
                    page_dto: NotebookPageDTO = self.agent.ask_page_vision_structured(
                        page_artifact=art,
                        prompt=page_prompt,
                        schema=self.page_schema,
                        max_tokens=max_tokens,
                        **call_kwargs,
                    )
                except StructuredOutputValidationError:
                    # Do not guess a conversion from pixel-like coordinates.
                    # Retry once with an explicit schema correction instead.
                    correction = (
                        "\nCORRECCION OBLIGATORIA: las coordenadas visuales de diagrams "
                        "deben ser numeros normalizados entre 0.0 y 1.0. No uses pixeles. "
                        "Si no puedes expresarlas con evidencia, devuelve diagrams=[]."
                    )
                    try:
                        page_dto = self.agent.ask_page_vision_structured(
                            page_artifact=art,
                            prompt=page_prompt + correction,
                            schema=self.page_schema,
                            max_tokens=max_tokens,
                            **call_kwargs,
                        )
                    except StructuredOutputValidationError:
                        # Preserve textual evidence when the model cannot emit valid
                        # normalized geometry. Never derive geometry from pixel values.
                        page_dto = self.agent.ask_page_vision_structured(
                            page_artifact=art,
                            prompt=(
                                page_prompt
                                + "\nNo extraigas diagramas en esta respuesta: devuelve diagrams=[] "
                                "y conserva fielmente el resto de la página."
                            ),
                            schema=self.page_schema,
                            max_tokens=max_tokens,
                            **call_kwargs,
                        )

                page_dto = self.normalize_page_dto(page_dto)
                page_dto = self.enrich_page_dto(art, page_dto, max_tokens, call_kwargs)

                if isinstance(page_dto, NotebookPageDTO):
                    page_dtos.append(page_dto)

                try:
                    page_data = dto_to_notebook_page(page_dto, page_number=art.page_number)
                except Exception:
                    if not page_dto.diagrams:
                        raise
                    # Preserve the rest of the page; invalid diagram geometry is not
                    # safe to repair or infer from the model response.
                    page_dto = page_dto.model_copy(update={"diagrams": []})
                    page_data = dto_to_notebook_page(page_dto, page_number=art.page_number)
                extracted_pages.append(page_data)

            # 3. Fusión pura de páginas y reconciliación de cabecera
            if progress:
                progress.set_phase(5, "Fusión y normalización", "Reconciliando páginas y validando...")
            merged_doc = merge_notebook_pages(
                pages=extracted_pages,
                source_file=str(input_path),
                config=self.config,
            )

            # 4. Validación determinista y normalización
            validated_doc = validate_notebook_document(
                doc=merged_doc,
                config=self.config,
            )

            # 5. Renderizado y persistencia de derivados gráficos de diagramas
            if progress:
                progress.set_phase(6, "Generación de entregables", f"Renderizando {self.document_filename} y derivados...")
            assets_map: dict[str, str] = {}
            for page in validated_doc.pages:
                for idx, diag in enumerate(page.diagrams):
                    diag_key = f"p{diag.source_page}_d{idx}"
                    if diag.diagram_type == "flowchart":
                        if self.config.render_diagram_visuals:
                            mmd_content = render_mermaid(diag)
                            mmd_filename = f"diagram_p{diag.source_page:03d}_{idx:02d}.mmd"
                            write_atomic_file(staging_assets_dir / mmd_filename, mmd_content)
                            assets_map[diag_key] = f"assets/{mmd_filename}"
                    else:
                        if self.config.render_diagram_visuals:
                            svg_content = render_svg(diag)
                            svg_filename = f"sketch_p{diag.source_page:03d}_{idx:02d}.svg"
                            write_atomic_file(staging_assets_dir / svg_filename, svg_content)
                            assets_map[diag_key] = f"assets/{svg_filename}"

            # 6. Renderizado Markdown determinista (2 tablas obligatorias de AGENTS.md + secciones flexibles)
            markdown_output = self.render_document(validated_doc, assets_map)

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
            notebook_path = staging_dir / self.document_filename
            document_json_path = staging_dir / "document.json"
            issues_json_path = staging_review_dir / "issues.json"

            write_atomic_file(notebook_path, markdown_output)
            write_atomic_file(document_json_path, validated_doc.model_dump_json(indent=2))
            write_atomic_file(issues_json_path, review_issues_to_json(review_issues))

            # 9. Publicación atómica de staging a canonical_dir con respaldo y rollback seguro
            if progress:
                progress.set_phase(7, "Publicación canónica", f"Guardando en {canonical_dir.name}")
            if canonical_dir.exists():
                backup_dir = (self.output_base_dir / f".backup_notebook_{stem}_{uuid.uuid4().hex}").resolve()
                backup_dir.relative_to(self.output_base_dir)
                shutil.move(str(canonical_dir), str(backup_dir))

            try:
                shutil.move(str(staging_dir), str(canonical_dir))
            except Exception as move_exc:
                if canonical_dir.exists():
                    try:
                        canonical_dir.relative_to(self.output_base_dir)
                        shutil.rmtree(str(canonical_dir), ignore_errors=True)
                    except ValueError:
                        pass

                if backup_dir and backup_dir.exists():
                    try:
                        backup_dir.relative_to(self.output_base_dir)
                        shutil.move(str(backup_dir), str(canonical_dir))
                    except Exception as rollback_exc:
                        raise NotebookRollbackError(
                            f"Fallo crítico durante el swap de publicación a '{canonical_dir}' "
                            f"y la restauración automática del backup en '{backup_dir}' falló. "
                            f"El backup ha sido conservado intacto para recuperación manual.",
                            backup_dir=backup_dir,
                            original_error=move_exc,
                        ) from rollback_exc

                raise move_exc

            # Éxito: limpiar backup si existió
            if backup_dir and backup_dir.exists():
                shutil.rmtree(str(backup_dir), ignore_errors=True)

            if progress:
                progress.finish("Completado exitosamente")

            return markdown_output, validated_doc

        except NotebookRollbackError:
            if staging_dir.exists():
                try:
                    staging_dir.relative_to(self.output_base_dir)
                    shutil.rmtree(str(staging_dir), ignore_errors=True)
                except ValueError:
                    pass
            raise

        except Exception as exc:
            if staging_dir.exists():
                try:
                    staging_dir.relative_to(self.output_base_dir)
                    shutil.rmtree(str(staging_dir), ignore_errors=True)
                except ValueError:
                    pass

            if backup_dir and backup_dir.exists():
                try:
                    backup_dir.relative_to(self.output_base_dir)
                    if canonical_dir.exists():
                        canonical_dir.relative_to(self.output_base_dir)
                        shutil.rmtree(str(canonical_dir), ignore_errors=True)
                    shutil.move(str(backup_dir), str(canonical_dir))
                except Exception as rollback_exc:
                    raise NotebookRollbackError(
                        f"Fallo durante el procesamiento en '{canonical_dir}' "
                        f"y la restauración del backup en '{backup_dir}' falló. "
                        f"El backup ha sido conservado intacto.",
                        backup_dir=backup_dir,
                        original_error=exc,
                    ) from rollback_exc
            raise

class CuadernoCampoProfile(NotebookProfile):
    """Perfil visual secuencial sin el contrato ni las tablas de estadillo."""

    document_filename = "cuaderno_campo.md"
    staging_prefix = "cuaderno_campo"
    page_schema = CuadernoTextPageDTO

    def build_page_prompt(self, page_number: int, prompt: Optional[str]) -> str:
        base = super().build_page_prompt(page_number, prompt)
        if not self.config.is_diagram_extraction_enabled(page_number):
            return base + (
                "\nMODO CUADERNO_CAMPO (TEXTO PURO): La extracción de diagramas está DESACTIVADA para esta página. "
                "Transcribe de forma limpia, clara, completa y ordenada TODO el texto de la página (títulos, secciones, párrafos, notas, listas, tablas y texto en recuadros). "
                "Devuelve estadillo_rows=[] y diagrams=[]."
            )
        return base + (
            "\nMODO CUADERNO_CAMPO: conserva texto y tablas generales, pero devuelve "
            "estadillo_rows=[] y diagrams=[]; las figuras se extraen en una fase VLM separada."
        )

    def enrich_page_dto(self, art: Any, page_dto: NotebookPageDTO, max_tokens: int, call_kwargs: dict[str, Any]) -> NotebookPageDTO:
        if not self.config.is_diagram_extraction_enabled(art.page_number):
            return page_dto.model_copy(update={"estadillo_rows": [], "diagrams": []})

        from ..diagrams.extraction import get_diagram_prompt

        inventory = self.agent.ask_page_vision_structured(
            page_artifact=art, prompt=VISUAL_INVENTORY_PROMPT, schema=VisualInventoryDTO,
            max_tokens=min(max_tokens, 1024), **call_kwargs,
        )
        diagrams: list[DiagramDTO] = []
        for index, item in enumerate(inventory.items):
            diagram_prompt = get_diagram_prompt(item.visual_type, art.page_number) + (
                f"\nELEMENTO OBJETIVO {index + 1}: {item.description}. "
                "Extrae solo este elemento y respeta coordenadas relativas entre 0.0 y 1.0."
            )
            diagram = self.agent.ask_page_vision_structured(
                page_artifact=art, prompt=diagram_prompt, schema=DiagramDTO,
                max_tokens=max_tokens, **call_kwargs,
            )
            canonical = dto_to_diagram_ir(diagram, source_page=art.page_number)
            if item.visual_type == "flowchart":
                has_named_nodes = any(point.label for point in canonical.points)
                has_edges = bool(canonical.relations) or any(
                    line.source_point_id and line.target_point_id for line in canonical.lines
                )
                if not (has_named_nodes and has_edges):
                    retry_prompt = diagram_prompt + (
                        "\nCORRECCION DE FLUJO OBLIGATORIA: representa cada caja como un point con "
                        "label igual al texto visible; representa cada flecha como line con "
                        "source_point_id y target_point_id válidos. No uses areas sin bbox/polygon. "
                        "Usa coordenadas decimales 0.0..1.0 y no omitas las conexiones visibles."
                    )
                    retry = self.agent.ask_page_vision_structured(
                        page_artifact=art, prompt=retry_prompt, schema=DiagramDTO,
                        max_tokens=max_tokens, **call_kwargs,
                    )
                    dto_to_diagram_ir(retry, source_page=art.page_number)
                    diagram = retry
            diagrams.append(diagram)
        return page_dto.model_copy(update={"estadillo_rows": [], "diagrams": diagrams})

    def render_document(self, doc: NotebookDocument, assets_map: dict[str, str]) -> str:
        from ..render.cuaderno_campo import render_cuaderno_campo_markdown
        return render_cuaderno_campo_markdown(doc=doc, config=self.config, assets_map=assets_map)
