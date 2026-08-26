import math
from typing import Optional, Union
from pydantic import BaseModel, Field, ConfigDict, model_validator

from .evidence import EvidenceValue
from .warnings import ExtractionWarning
from .estadillo import EstadilloPage, EstadilloPageHeader, EstadilloRow
from .review import NormalizedBBox

_BBOX1000_MAX = 1000


class CandidateVisualBBox1000(BaseModel):
    """Bounding box entero candidato 0..1000 recibido de la respuesta VLM.

    Permite parsear la extracción tabular completa incluso si el modelo comete
    un error puntual en las coordenadas visuales opcionales (ej: y0 >= y1 o coords > 1000).
    La validación geométrica rigurosa se realiza de forma aislada al recolectar
    evidencia visual: si es válida se promueve a VisualBBox1000 y NormalizedBBox;
    si es inválida se descarta deterministamente sin crop ni fallo de extracción.
    """
    x0: int = Field(..., description="Borde izquierdo candidato en cuadrícula 0..1000")
    y0: int = Field(..., description="Borde superior candidato en cuadrícula 0..1000")
    x1: int = Field(..., description="Borde derecho candidato en cuadrícula 0..1000")
    y1: int = Field(..., description="Borde inferior candidato en cuadrícula 0..1000")

    model_config = ConfigDict(extra="forbid", strict=True)


class VisualBBox1000(CandidateVisualBBox1000):
    """Bounding box validado en cuadrícula entera 0..1000 (0<=coords<=1000, x0<x1, y0<y1).

    Garantiza estricta no degeneración y confinamiento en [0, 1000].
    Conversión canónica: dividir cada campo entre 1000 → NormalizedBBox [0,1].
    """
    x0: int = Field(..., ge=0, le=_BBOX1000_MAX, description="Borde izquierdo en cuadrícula 0..1000")
    y0: int = Field(..., ge=0, le=_BBOX1000_MAX, description="Borde superior en cuadrícula 0..1000")
    x1: int = Field(..., ge=0, le=_BBOX1000_MAX, description="Borde derecho en cuadrícula 0..1000")
    y1: int = Field(..., ge=0, le=_BBOX1000_MAX, description="Borde inferior en cuadrícula 0..1000")

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_box_order(self) -> "VisualBBox1000":
        """Valida que x0<x1 y y0<y1 (caja estrictamente no degenerada)."""
        if self.x0 >= self.x1:
            raise ValueError(
                f"VisualBBox1000: x0 ({self.x0}) debe ser estrictamente menor que x1 ({self.x1})."
            )
        if self.y0 >= self.y1:
            raise ValueError(
                f"VisualBBox1000: y0 ({self.y0}) debe ser estrictamente menor que y1 ({self.y1})."
            )
        return self


def validate_visual_bbox1000(
    candidate: Union[VisualBBox1000, CandidateVisualBBox1000, None]
) -> Optional[VisualBBox1000]:
    """Valida estrictamente un bounding box candidato 0..1000.

    Requisitos para ser válido:
    1. Instancia no nula.
    2. Tipos enteros estrictos (no bool/str/float).
    3. Límites en [0, 1000] inclusive.
    4. Geometría estrictamente no degenerada: x0 < x1 e y0 < y1.

    Si no cumple todas las condiciones, devuelve None (descarte determinista sin fallar la extracción).
    """
    if candidate is None:
        return None
    if isinstance(candidate, VisualBBox1000):
        return candidate
    # Verificar tipos enteros puros (en Python isinstance(True, int) es True)
    if (
        type(candidate.x0) is not int
        or type(candidate.y0) is not int
        or type(candidate.x1) is not int
        or type(candidate.y1) is not int
    ):
        return None
    # Verificar rango [0, 1000]
    if not (
        0 <= candidate.x0 <= _BBOX1000_MAX
        and 0 <= candidate.y0 <= _BBOX1000_MAX
        and 0 <= candidate.x1 <= _BBOX1000_MAX
        and 0 <= candidate.y1 <= _BBOX1000_MAX
    ):
        return None
    # Verificar orden estricto
    if candidate.x0 >= candidate.x1 or candidate.y0 >= candidate.y1:
        return None
    return VisualBBox1000(
        x0=candidate.x0,
        y0=candidate.y0,
        x1=candidate.x1,
        y1=candidate.y1,
    )


def visual_bbox1000_to_normalized(
    bbox: Union[VisualBBox1000, CandidateVisualBBox1000]
) -> NormalizedBBox:
    """Convierte de forma pura y determinista un VisualBBox1000 al NormalizedBBox canónico [0,1].

    División exacta por 1000. No realiza clamp ni extrapolación.
    Si se recibe un CandidateVisualBBox1000, se valida previamente. Lanza ValueError
    si el bbox no cumple las invariantes geométricas y de rango.
    """
    valid_box = validate_visual_bbox1000(bbox)
    if valid_box is None:
        raise ValueError(
            f"No se puede convertir a NormalizedBBox: VisualBBox1000 inválido o fuera de límites: {bbox}"
        )
    return NormalizedBBox(
        x0=round(valid_box.x0 / _BBOX1000_MAX, 6),
        y0=round(valid_box.y0 / _BBOX1000_MAX, 6),
        x1=round(valid_box.x1 / _BBOX1000_MAX, 6),
        y1=round(valid_box.y1 / _BBOX1000_MAX, 6),
    )


class VisualRegion1000DTO(BaseModel):
    """Región visual en cuadrícula 1000 para el contrato VLM (transitoria, no canónica).

    El VLM emite coordenadas enteras 0..1000 para las regiones de evidencia dudosa.
    Se valida individualmente antes de generar ReviewIssue o crops.
    """
    page: Optional[int] = Field(default=None, ge=1, description="Número de página (debe coincidir con la página contenedora)")
    field: Optional[str] = Field(default=None, description="Nombre del campo dudoso asociado (ej: 'altura_cm', 'especie')")
    row_key: Optional[str] = Field(default=None, description="Clave de fila (ej: 'col1_fil26')")
    row_index: Optional[int] = Field(default=None, ge=0, description="Índice de fila 0-based en la página")
    bbox: CandidateVisualBBox1000 = Field(..., description="Coordenadas enteras 0..1000 de la región (relativas a imagen completa)")

    model_config = ConfigDict(extra="forbid", strict=True)


class EstadilloHeaderDTO(BaseModel):
    """Metadatos compactos de cabecera para extracción VLM estructurada."""
    objetivo: Optional[str] = None
    fecha: Optional[str] = None
    asistentes: Optional[str] = None
    equipamiento: Optional[str] = None
    situacion_atmosferica: Optional[str] = None
    especies_declaradas: Optional[str] = None
    field_bboxes: Optional[dict[str, CandidateVisualBBox1000]] = None

    model_config = ConfigDict(extra="forbid", strict=True)


class EstadilloRowDTO(BaseModel):
    """Fila compacta y eficiente en tokens para extracción VLM."""
    id: Optional[Union[int, str]] = None
    col: Optional[Union[int, str]] = None
    fil: Optional[Union[int, str]] = None
    especie: Optional[str] = None
    altura_cm: Optional[Union[float, int, str]] = None
    foto: Optional[Union[int, str]] = None
    bbch: Optional[Union[float, int, str]] = None
    observaciones: Optional[str] = None
    uncertain_fields: list[str] = Field(default_factory=list)
    bbox: Optional[CandidateVisualBBox1000] = None
    field_bboxes: Optional[dict[str, CandidateVisualBBox1000]] = None

    model_config = ConfigDict(extra="forbid", strict=True)


class EstadilloPageDTO(BaseModel):
    """Contrato estructurado compacto por página para inferencia multimodal."""
    page_number: int = Field(..., ge=1)
    header: Optional[EstadilloHeaderDTO] = None
    rows: list[EstadilloRowDTO] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    additional_text: Optional[str] = None
    regions: list[VisualRegion1000DTO] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)



_VALID_ROW_FIELDS = frozenset({
    "id",
    "col",
    "fil",
    "especie",
    "altura_cm",
    "foto",
    "bbch",
    "observaciones",
})


def _convert_str_evidence(
    val: Optional[Union[int, float, str]],
    field_name: str,
    page_number: int,
    uncertain_fields: frozenset[str],
) -> Optional[EvidenceValue[str]]:
    """Convierte un campo de texto/identificador a EvidenceValue[str] sin inventar celdas ausentes."""
    if val is None:
        return None
    raw_str = str(val).strip()
    if not raw_str:
        return None
    is_uncertain = field_name in uncertain_fields and field_name in _VALID_ROW_FIELDS
    return EvidenceValue[str](
        raw=raw_str,
        normalized=raw_str,
        source_page=page_number,
        uncertain=is_uncertain,
    )


def _convert_int_evidence(
    val: Optional[Union[int, str]],
    field_name: str,
    page_number: int,
    uncertain_fields: frozenset[str],
) -> Optional[EvidenceValue[int]]:
    """Convierte un campo entero (col, fil) a EvidenceValue[int]."""
    if val is None:
        return None
    raw_str = str(val).strip()
    if not raw_str:
        return None
    is_uncertain = field_name in uncertain_fields and field_name in _VALID_ROW_FIELDS
    norm_int: Optional[int] = None
    try:
        norm_int = int(raw_str)
    except (ValueError, TypeError):
        is_uncertain = True
    return EvidenceValue[int](
        raw=raw_str,
        normalized=norm_int,
        source_page=page_number,
        uncertain=is_uncertain,
    )


def _convert_float_evidence(
    val: Optional[Union[float, int, str]],
    field_name: str,
    page_number: int,
    uncertain_fields: frozenset[str],
) -> Optional[EvidenceValue[float]]:
    """Convierte un campo decimal (altura_cm) a EvidenceValue[float]."""
    if val is None:
        return None
    raw_str = str(val).strip()
    if not raw_str:
        return None
    is_uncertain = field_name in uncertain_fields and field_name in _VALID_ROW_FIELDS
    norm_float: Optional[float] = None
    try:
        norm_float = float(raw_str.replace(",", "."))
        if not math.isfinite(norm_float):
            norm_float = None
            is_uncertain = True
    except (ValueError, TypeError):
        is_uncertain = True
    return EvidenceValue[float](
        raw=raw_str,
        normalized=norm_float,
        source_page=page_number,
        uncertain=is_uncertain,
    )


def dto_to_estadillo_page(dto: Union[EstadilloPageDTO, EstadilloPage], page_number: int) -> EstadilloPage:
    """Convierte de forma pura y determinista un DTO compacto de extracción al modelo de dominio canónico EstadilloPage.

    La procedencia `source_page` se deriva inequívocamente del contexto contractual de la página analizada,
    sin inventar evidencia visual ni mutar la estructura del DTO.

    Rechaza explícitamente cualquier discrepancia entre `dto.page_number` y el `page_number` esperado.
    """
    if isinstance(dto, EstadilloPage):
        if dto.page_number != page_number:
            raise ValueError(
                f"Discrepancia de número de página en EstadilloPage: recibido={dto.page_number}, esperado={page_number}"
            )
        return dto

    if dto.page_number != page_number:
        raise ValueError(
            f"Discrepancia de número de página en DTO: recibido={dto.page_number}, esperado={page_number}"
        )

    # 1. Conversión de cabecera
    canonical_header: Optional[EstadilloPageHeader] = None
    if dto.header is not None:
        empty_unc: frozenset[str] = frozenset()
        obj_ev = _convert_str_evidence(dto.header.objetivo, "objetivo", page_number, empty_unc)
        fec_ev = _convert_str_evidence(dto.header.fecha, "fecha", page_number, empty_unc)
        asi_ev = _convert_str_evidence(dto.header.asistentes, "asistentes", page_number, empty_unc)
        eq_ev = _convert_str_evidence(dto.header.equipamiento, "equipamiento", page_number, empty_unc)
        atm_ev = _convert_str_evidence(dto.header.situacion_atmosferica, "situacion_atmosferica", page_number, empty_unc)
        esp_ev = _convert_str_evidence(dto.header.especies_declaradas, "especies_declaradas", page_number, empty_unc)

        if any(ev is not None for ev in (obj_ev, fec_ev, asi_ev, eq_ev, atm_ev, esp_ev)):
            canonical_header = EstadilloPageHeader(
                source_page=page_number,
                objetivo=obj_ev,
                fecha=fec_ev,
                asistentes=asi_ev,
                equipamiento=eq_ev,
                situacion_atmosferica=atm_ev,
                especies_declaradas=esp_ev,
            )

    # 2. Conversión de filas
    canonical_rows: list[EstadilloRow] = []
    for r in dto.rows:
        unc = frozenset(f for f in (r.uncertain_fields or []) if f in _VALID_ROW_FIELDS)
        row_id = _convert_str_evidence(r.id, "id", page_number, unc)
        row_col = _convert_int_evidence(r.col, "col", page_number, unc)
        row_fil = _convert_int_evidence(r.fil, "fil", page_number, unc)
        row_esp = _convert_str_evidence(r.especie, "especie", page_number, unc)
        row_alt = _convert_float_evidence(r.altura_cm, "altura_cm", page_number, unc)
        row_foto = _convert_str_evidence(r.foto, "foto", page_number, unc)
        row_bbch = _convert_str_evidence(r.bbch, "bbch", page_number, unc)
        row_obs = _convert_str_evidence(r.observaciones, "observaciones", page_number, unc)

        # Si al menos un campo tiene dato
        if any(ev is not None for ev in (row_id, row_col, row_fil, row_esp, row_alt, row_foto, row_bbch, row_obs)):
            canonical_rows.append(
                EstadilloRow(
                    source_page=page_number,
                    id=row_id,
                    col=row_col,
                    fil=row_fil,
                    especie=row_esp,
                    altura_cm=row_alt,
                    foto=row_foto,
                    bbch=row_bbch,
                    observaciones=row_obs,
                )
            )

    # 3. Conversión de advertencias
    canonical_warnings = [
        ExtractionWarning(code="VLM_WARNING", message=str(w), source_page=page_number)
        for w in (dto.warnings or [])
        if str(w).strip()
    ]

    return EstadilloPage(
        page_number=page_number,
        header=canonical_header,
        rows=canonical_rows,
        additional_text=dto.additional_text,
        warnings=canonical_warnings,
    )