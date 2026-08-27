import os
import shutil
import uuid
from pathlib import Path
from typing import Optional, Union, Any, Tuple, Dict

from ..schemas.diagram import (
    DiagramType,
    DiagramIR,
    DiagramDTO,
    dto_to_diagram_ir,
)
from ..schemas.warnings import ExtractionWarning
from ..artifacts import PageArtifact
from ..vlm.lmstudio import LMStudioClient, encode_image_to_data_uri
from ..profiles.estadillo import safe_document_stem, write_atomic_file
from .validation import validate_diagram_ir

DIAGRAM_PROMPT_TEMPLATE = """Extrae de forma rigurosa y estructurada el diagrama o croquis visible en esta imagen.

REGLAS CONTRACTUALES OBLIGATORIAS:
1. FUENTE PRIMARIA: La imagen es la fuente primaria y exclusiva de evidencia visual. El texto OCR es únicamente una hipótesis de apoyo.
2. NO CÓDIGO GRÁFICO: Devuelve EXCLUSIVAMENTE el objeto JSON estructurado conforme al esquema 'DiagramDTO'. NO generes SVG, Mermaid, HTML, ni bloques de formato gráfico.
3. COORDENADAS VISUALES [0.0, 1.0]:
   - Todas las coordenadas x e y de puntos, líneas y áreas representan ÚNICAMENTE la posición gráfica relativa en el dibujo, donde (0.0, 0.0) es la esquina superior izquierda y (1.0, 1.0) la esquina inferior derecha.
   - NUNCA uses coordenadas GPS ni inventes proyecciones geográficas en campos visuales.
4. COORDENADAS GEOGRÁFICAS (GPS):
   - NUNCA infieras coordenadas GPS a partir de la disposición visual, escalas aproximadas o topónimos.
   - Establece georeferenced=true ÚNICAMENTE si existen coordenadas geográficas (latitud/longitud) numéricas explícitas y legibles en el documento.
   - Si georeferenced=true, proporciona el CRS explícito (ej: 'EPSG:4326', 'ETRS89 / UTM 30N') y añade cada coordenada en 'geographic_coordinates' con su 'raw_text' legible. Si georeferenced=false, crs debe ser null y geographic_coordinates [].
5. ORIENTACIÓN:
   - Si el croquis no incluye una flecha de norte o indicación explícita de orientación con evidencia textual, establece orientation.direction='unknown' o null. NO la asumas ni la inventes.
   - Si orientation.direction='rotated', proporciona 'degrees' numérico explícito.
6. ENTIDADES Y RELACIONES:
   - Identifica áreas ('areas'), puntos/nodos ('points'), líneas/conectores ('lines'), etiquetas legibles ('labels') y relaciones explícitas ('relations').
   - Asigna identificadores únicos en todo el diagrama (ej: 'P1', 'P2', 'L1', 'A1', 'lbl1', 'rel1').
   - Asegura que todas las referencias internas (source_id, target_id, source_point_id, target_point_id, attached_to_id, associated_point_id) apunten a IDs existentes del tipo adecuado (source_point_id y target_point_id deben ser puntos).
7. ILEGIBILIDAD E INCERTIDUMBRE:
   - Si un texto, símbolo o elemento es dudoso o ilegible, no lo inventes: usa null o cadenas vacías y marca uncertain=true.
8. TIPO DE DIAGRAMA:
   - diagram_type debe ser exactamente '{diagram_type}'.
   - source_page debe ser exactamente {page_number}.
"""

DEFAULT_DIAGRAM_EXTRA_BODY: dict[str, Any] = {
    "chat_template_kwargs": {"enable_thinking": False},
    "enable_thinking": False,
    "reasoning_effort": "none",
}

_VALID_DIAGRAM_TYPES = frozenset({"field_sketch", "flowchart", "gps_sketch"})


def get_diagram_prompt(
    diagram_type: DiagramType,
    page_number: int = 1,
) -> str:
    """Genera el prompt contractual determinista para extracción estructurada de diagramas.

    diagram_type es obligatorio y debe ser exactamente 'field_sketch', 'flowchart' o 'gps_sketch'.
    Lanza ValueError descriptivo si es None o inválido (no se infiere ni inventa el tipo).
    """
    if not diagram_type or diagram_type not in _VALID_DIAGRAM_TYPES:
        raise ValueError(
            f"diagram_type es obligatorio y debe ser exactamente uno de {sorted(_VALID_DIAGRAM_TYPES)}, "
            f"recibido: {diagram_type!r}"
        )
    return DIAGRAM_PROMPT_TEMPLATE.format(
        diagram_type=diagram_type,
        page_number=page_number,
    )


def extract_diagram_from_image(
    image_path: Union[str, Path],
    client: LMStudioClient,
    diagram_type: DiagramType,
    page_number: int = 1,
    ocr_context: Optional[str] = None,
    prompt: Optional[str] = None,
    max_tokens: int = 4096,
    **kwargs: Any,
) -> DiagramIR:
    """Extrae un DiagramIR estructurado desde un archivo de imagen utilizando el VLM configurado.

    Valida estrictamente diagram_type antes de cualquier llamada a VLM.
    """
    if not diagram_type or diagram_type not in _VALID_DIAGRAM_TYPES:
        raise ValueError(
            f"diagram_type es obligatorio y debe ser exactamente uno de {sorted(_VALID_DIAGRAM_TYPES)}, "
            f"recibido: {diagram_type!r}"
        )

    img_path = Path(image_path).resolve()
    if not img_path.is_file():
        raise FileNotFoundError(f"Archivo de imagen no encontrado o no es un archivo regular: {img_path}")

    # Validar formato y firmas mágicas usando la utilidad de bajo nivel de LMStudioClient
    encode_image_to_data_uri(img_path)

    effective_prompt = (
        prompt
        if prompt is not None
        else get_diagram_prompt(diagram_type=diagram_type, page_number=page_number)
    )

    call_kwargs = dict(kwargs)
    call_kwargs.setdefault("reasoning_effort", "none")

    user_extra_body = kwargs.get("extra_body")
    effective_extra_body = dict(DEFAULT_DIAGRAM_EXTRA_BODY)
    if user_extra_body is not None:
        effective_extra_body.update(user_extra_body)
        if "chat_template_kwargs" in user_extra_body and isinstance(user_extra_body["chat_template_kwargs"], dict):
            merged_chat_kwargs = dict(DEFAULT_DIAGRAM_EXTRA_BODY["chat_template_kwargs"])
            merged_chat_kwargs.update(user_extra_body["chat_template_kwargs"])
            effective_extra_body["chat_template_kwargs"] = merged_chat_kwargs
    call_kwargs["extra_body"] = effective_extra_body

    dto: DiagramDTO = client.ask_vision_structured(
        image_path=img_path,
        prompt=effective_prompt,
        schema=DiagramDTO,
        ocr_context=ocr_context,
        max_tokens=max_tokens,
        **call_kwargs,
    )

    canonical_ir = dto_to_diagram_ir(dto, source_page=page_number)
    validate_diagram_ir(canonical_ir, raise_on_error=True)
    return canonical_ir


def extract_diagram_from_page(
    page_artifact: PageArtifact,
    client: LMStudioClient,
    diagram_type: DiagramType,
    prompt: Optional[str] = None,
    max_tokens: int = 4096,
    **kwargs: Any,
) -> DiagramIR:
    """Extrae un DiagramIR estructurado desde un PageArtifact conservando OCR complementario y provenance."""
    if not page_artifact.image_path or not page_artifact.image_path.is_file():
        raise FileNotFoundError(f"PageArtifact no contiene una ruta de imagen válida: {page_artifact}")

    return extract_diagram_from_image(
        image_path=page_artifact.image_path,
        client=client,
        diagram_type=diagram_type,
        page_number=page_artifact.page_number,
        ocr_context=page_artifact.raw_ocr,
        prompt=prompt,
        max_tokens=max_tokens,
        **kwargs,
    )


def persist_diagram_artifacts(
    diagram: DiagramIR,
    source_image_path: Union[str, Path],
    output_base_dir: Union[str, Path],
    document_name: Optional[str] = None,
) -> dict[str, Path]:
    """Persiste atómicamente DiagramIR como JSON y una copia exacta de la imagen fuente en assets/.

    Garantías de seguridad y robustez:
    1. Confinamiento estricto: Todo archivo generado reside bajo output_base_dir.
    2. Validación de nombre de documento: Sanea document_name y rechaza traversal, nombres reservados o vacíos.
    3. Cero renderizado gráfico: NO genera .svg, .mmd ni .mermaid (reservado para PR10).
    4. Staging aislado: Toda escritura ocurre en un directorio de staging temporal.
    5. Rollback determinista: Si ocurre cualquier fallo, se limpia cualquier directorio canónico parcial
       y se restaura la versión previa del backup sin dejar restos de staging ni backups.
    6. Copia exacta de imagen original preservada byte a byte en assets/<stem>_original.<ext>.
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
        # Rechazar explícitamente secuencias de escape de directorio y separadores
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

    staging_dir = (base_out / f".staging_diagram_{stem}_{uuid.uuid4().hex}").resolve()
    staging_dir.relative_to(base_out)
    backup_dir: Optional[Path] = None

    try:
        staging_assets_dir = staging_dir / "assets"
        staging_assets_dir.mkdir(parents=True, exist_ok=True)

        # Copia exacta de la imagen original en assets/
        img_ext = in_img.suffix.lower() if in_img.suffix else ".png"
        target_img_name = f"{stem}_original{img_ext}"
        staging_img_path = staging_assets_dir / target_img_name
        shutil.copy2(in_img, staging_img_path)

        # Escritura atómica de diagram.json
        staging_json_path = staging_dir / "diagram.json"
        json_content = diagram.model_dump_json(indent=2)
        write_atomic_file(staging_json_path, json_content)

        # Verificación de que no se crearon archivos gráficos
        for created_file in staging_dir.rglob("*"):
            if created_file.is_file():
                if created_file.suffix.lower() in (".svg", ".mmd", ".mermaid"):
                    raise RuntimeError(
                        f"Violación de alcance PR9: archivo gráfico no permitido generado: {created_file.name}"
                    )

        # Publicación atómica con rollback
        if canonical_dir.exists():
            backup_dir = (base_out / f".backup_diagram_{stem}_{uuid.uuid4().hex}").resolve()
            backup_dir.relative_to(base_out)
            shutil.move(str(canonical_dir), str(backup_dir))

        try:
            shutil.move(str(staging_dir), str(canonical_dir))
            if backup_dir and backup_dir.exists():
                shutil.rmtree(str(backup_dir), ignore_errors=True)
        except Exception:
            # Si falló la publicación de staging a canonical y quedó un canonical corrupto/parcial
            if canonical_dir.exists():
                canonical_dir.relative_to(base_out)
                shutil.rmtree(str(canonical_dir), ignore_errors=True)
            if backup_dir and backup_dir.exists():
                backup_dir.relative_to(base_out)
                shutil.move(str(backup_dir), str(canonical_dir))
            raise

        published_json = canonical_dir / "diagram.json"
        published_img = canonical_dir / "assets" / target_img_name

        return {
            "canonical_dir": canonical_dir,
            "diagram_json": published_json,
            "original_image": published_img,
        }

    except Exception:
        # En caso de fallo en cualquier punto:
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
            except Exception:
                pass
        raise


def process_diagram(
    image_path: Union[str, Path],
    client: LMStudioClient,
    diagram_type: DiagramType,
    output_base_dir: Optional[Union[str, Path]] = None,
    page_number: int = 1,
    ocr_context: Optional[str] = None,
    prompt: Optional[str] = None,
    max_tokens: int = 4096,
    **kwargs: Any,
) -> Tuple[DiagramIR, Optional[dict[str, Path]]]:
    """Extrae un DiagramIR estructurado y opcionalmente persiste los artefactos de forma atómica y confinada."""
    diagram = extract_diagram_from_image(
        image_path=image_path,
        client=client,
        diagram_type=diagram_type,
        page_number=page_number,
        ocr_context=ocr_context,
        prompt=prompt,
        max_tokens=max_tokens,
        **kwargs,
    )

    artifacts = None
    if output_base_dir is not None:
        artifacts = persist_diagram_artifacts(
            diagram=diagram,
            source_image_path=image_path,
            output_base_dir=output_base_dir,
        )

    return diagram, artifacts
