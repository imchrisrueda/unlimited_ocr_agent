import math
import posixpath
import re
from pathlib import Path
from typing import Optional, Any, Dict, List, Union
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from src.fieldnotes.schemas.estadillo import EstadilloDocument


def validate_confined_relative_path(path_str: str, base_dir: Path) -> Path:
    """Valida que una ruta relativa esté estrictamente confinada dentro de base_dir.

    Rechaza rutas absolutas, secuencias de escape '..' y accesos fuera del árbol del dataset.
    """
    if not path_str or not isinstance(path_str, str):
        raise ValueError(f"Ruta inválida: {path_str!r}")

    clean_path = path_str.strip()
    if "\\" in clean_path:
        raise ValueError(f"Las rutas del benchmark deben usar separadores POSIX '/' exclusivamente: '{clean_path}'")

    if clean_path.startswith("/") or re.match(r"^[a-zA-Z]:", clean_path):
        raise ValueError(f"La ruta debe ser relativa al dataset, no absoluta: '{clean_path}'")

    norm = posixpath.normpath(clean_path)
    if norm.startswith("..") or "/../" in f"/{clean_path}/":
        raise ValueError(f"La ruta contiene secuencias de escape no permitidas '..': '{clean_path}'")

    base_resolved = base_dir.resolve()
    target_resolved = (base_resolved / norm).resolve()

    try:
        target_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"Ruta '{clean_path}' escapa del directorio base '{base_dir}'") from exc

    return target_resolved


class MetricValue(BaseModel):
    """Representación determinista y auditable de una métrica con conteos exactos."""
    numerator: int = Field(..., ge=0, description="Numerador (conteo de aciertos o elementos positivos)")
    denominator: int = Field(..., ge=0, description="Denominador (conteo total de elementos evaluados)")
    value: float = Field(..., ge=0.0, le=1.0, description="Valor del cociente en [0.0, 1.0]")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Desglose auditable de conteos")

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_invariants(self) -> "MetricValue":
        if not math.isfinite(self.value):
            raise ValueError(f"El valor de la métrica debe ser finito (no NaN/Inf), recibido: {self.value}")
        if not (0.0 <= self.value <= 1.0):
            raise ValueError(f"El valor de la métrica debe estar entre 0.0 y 1.0, recibido: {self.value}")
        if self.denominator == 0:
            if self.numerator != 0:
                raise ValueError(f"Invariante rota: numerador ({self.numerator}) > 0 con denominador 0")
        else:
            if self.numerator > self.denominator:
                raise ValueError(f"Invariante rota: numerador ({self.numerator}) > denominador ({self.denominator})")
        return self

    @classmethod
    def from_counts(
        cls,
        numerator: int,
        denominator: int,
        zero_denominator_value: float = 0.0,
        details: Optional[Dict[str, Any]] = None,
    ) -> "MetricValue":
        """Crea deterministamente un MetricValue validando estrictamente invariantes sin clamp."""
        if numerator < 0:
            raise ValueError(f"El numerador no puede ser negativo: {numerator}")
        if denominator < 0:
            raise ValueError(f"El denominador no puede ser negativo: {denominator}")

        if denominator == 0:
            if numerator != 0:
                raise ValueError(f"Invariante rota: numerador ({numerator}) > 0 con denominador 0")
            if not (0.0 <= zero_denominator_value <= 1.0):
                raise ValueError(f"zero_denominator_value debe estar en [0.0, 1.0], recibido: {zero_denominator_value}")
            val = float(zero_denominator_value)
        else:
            if numerator > denominator:
                raise ValueError(f"Invariante rota: numerador ({numerator}) excede el denominador ({denominator})")
            val = round(numerator / denominator, 6)

        return cls(
            numerator=numerator,
            denominator=denominator,
            value=val,
            details=details,
        )


class CaseMetrics(BaseModel):
    """Métricas cuantitativas de extracción calculadas sobre un caso individual del benchmark."""
    case_id: str = Field(..., min_length=1, description="Identificador único del caso evaluado")
    category: str = Field(..., min_length=1, description="Categoría del caso (easy, hard, handwritten, mixed, sketch)")
    pipeline_id: str = Field(..., min_length=1, description="Identificador de la configuración de pipeline")
    total_reference_rows: int = Field(..., ge=0, description="Total de filas en la verdad de referencia")
    total_predicted_rows: int = Field(..., ge=0, description="Total de filas en la predicción del pipeline")
    matched_rows: int = Field(..., ge=0, description="Total de filas emparejadas uno-a-uno")
    unmatched_reference_rows: int = Field(..., ge=0, description="Filas de referencia no emparejadas (falsos negativos)")
    unmatched_predicted_rows: int = Field(..., ge=0, description="Filas de predicción no emparejadas (falsos positivos/extras)")

    # 8 métricas obligatorias
    row_precision: MetricValue = Field(..., description="Precisión de filas: matched_rows / total_predicted_rows")
    row_recall: MetricValue = Field(..., description="Recall de filas: matched_rows / total_reference_rows")
    col_exact: MetricValue = Field(..., description="Exactitud de columna en pares emparejados")
    fil_exact: MetricValue = Field(..., description="Exactitud de fila en pares emparejados")
    species_exact: MetricValue = Field(..., description="Exactitud canónica de especie en pares emparejados")
    height_exact: MetricValue = Field(..., description="Exactitud exacta de altura en pares emparejados")
    photo_exact: MetricValue = Field(..., description="Exactitud de foto en pares emparejados")
    bbch_exact: MetricValue = Field(..., description="Exactitud exacta destacada de BBCH en pares emparejados")

    unmatched_reference_details: List[Dict[str, Any]] = Field(default_factory=list)
    unmatched_predicted_details: List[Dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_case_invariants(self) -> "CaseMetrics":
        if self.matched_rows > self.total_reference_rows:
            raise ValueError(f"matched_rows ({self.matched_rows}) > total_reference_rows ({self.total_reference_rows})")
        if self.matched_rows > self.total_predicted_rows:
            raise ValueError(f"matched_rows ({self.matched_rows}) > total_predicted_rows ({self.total_predicted_rows})")
        if self.unmatched_reference_rows != self.total_reference_rows - self.matched_rows:
            raise ValueError(f"unmatched_reference_rows ({self.unmatched_reference_rows}) != total_reference_rows - matched_rows")
        if self.unmatched_predicted_rows != self.total_predicted_rows - self.matched_rows:
            raise ValueError(f"unmatched_predicted_rows ({self.unmatched_predicted_rows}) != total_predicted_rows - matched_rows")
        if self.row_precision.numerator != self.matched_rows or self.row_precision.denominator != self.total_predicted_rows:
            raise ValueError("Invariante de conteo rota en row_precision")
        if self.row_recall.numerator != self.matched_rows or self.row_recall.denominator != self.total_reference_rows:
            raise ValueError("Invariante de conteo rota en row_recall")

        for m_name in ["col_exact", "fil_exact", "species_exact", "height_exact", "photo_exact", "bbch_exact"]:
            m: MetricValue = getattr(self, m_name)
            if m.numerator > m.denominator:
                raise ValueError(f"Invariante rota en {m_name}: numerator ({m.numerator}) > denominator ({m.denominator})")
            if m.denominator > self.total_reference_rows or m.denominator > self.total_predicted_rows:
                raise ValueError(f"Invariante rota en {m_name}: denominator ({m.denominator}) excede filas disponibles")
        return self


class AggregatedMetrics(BaseModel):
    """Métricas agregadas micro y macro por categoría o globales para un pipeline."""
    pipeline_id: str = Field(..., min_length=1)
    category: Optional[str] = Field(default=None, description="Categoría evaluada o None para agregación global")
    total_cases: int = Field(..., ge=0)
    total_reference_rows: int = Field(..., ge=0)
    total_predicted_rows: int = Field(..., ge=0)
    total_matched_rows: int = Field(..., ge=0)
    total_unmatched_reference_rows: int = Field(..., ge=0)
    total_unmatched_predicted_rows: int = Field(..., ge=0)

    # Micro agregaciones (sum of numerators / sum of denominators)
    row_precision_micro: MetricValue
    row_recall_micro: MetricValue
    col_exact_micro: MetricValue
    fil_exact_micro: MetricValue
    species_exact_micro: MetricValue
    height_exact_micro: MetricValue
    photo_exact_micro: MetricValue
    bbch_exact_micro: MetricValue

    # Macro agregaciones (mean of per-case values)
    row_precision_macro: MetricValue
    row_recall_macro: MetricValue
    col_exact_macro: MetricValue
    fil_exact_macro: MetricValue
    species_exact_macro: MetricValue
    height_exact_macro: MetricValue
    photo_exact_macro: MetricValue
    bbch_exact_macro: MetricValue

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_aggregated_invariants(self) -> "AggregatedMetrics":
        if self.total_matched_rows > self.total_reference_rows:
            raise ValueError("total_matched_rows > total_reference_rows")
        if self.total_matched_rows > self.total_predicted_rows:
            raise ValueError("total_matched_rows > total_predicted_rows")
        if self.total_unmatched_reference_rows != self.total_reference_rows - self.total_matched_rows:
            raise ValueError("total_unmatched_reference_rows inconsistente")
        if self.total_unmatched_predicted_rows != self.total_predicted_rows - self.total_matched_rows:
            raise ValueError("total_unmatched_predicted_rows inconsistente")
        if self.row_precision_micro.numerator != self.total_matched_rows or self.row_precision_micro.denominator != self.total_predicted_rows:
            raise ValueError("Invariante rota en row_precision_micro")
        if self.row_recall_micro.numerator != self.total_matched_rows or self.row_recall_micro.denominator != self.total_reference_rows:
            raise ValueError("Invariante rota en row_recall_micro")
        return self


class PipelineConfig(BaseModel):
    """Configuración tipada de un pipeline evaluado en el benchmark."""
    pipeline_id: str = Field(..., min_length=1, description="Identificador único del pipeline (ej: 'ocr_only', 'ocr_plus_vlm')")
    pipeline_name: str = Field(..., min_length=1, description="Nombre legible del pipeline")
    version: str = Field(..., min_length=1, description="Versión del pipeline")
    description: Optional[str] = None
    ocr_engine: Optional[str] = None
    vlm_model: Optional[str] = None
    profile: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", strict=True)


class BenchmarkCase(BaseModel):
    """Definición tipada de un caso individual de prueba en el manifiesto."""
    case_id: str = Field(..., min_length=1, description="Identificador único del caso")
    category: str = Field(..., min_length=1, description="Categoría (easy, hard, handwritten, mixed, sketch)")
    evaluate_structured: bool = Field(
        default=True,
        description="Indica si el caso se evalúa cuantitativamente a nivel estructural (filas y celdas)",
    )
    document_path: Optional[str] = Field(default=None, description="Ruta relativa POSIX al archivo PDF/imagen de entrada")
    ground_truth_path: Optional[str] = Field(default=None, description="Ruta relativa POSIX al JSON de verdad de referencia")
    predictions: Dict[str, str] = Field(default_factory=dict, description="Mapa pipeline_id -> ruta relativa POSIX al JSON de predicción")
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("ground_truth_path")
    @classmethod
    def validate_gt_path_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if "\\" in v or v.startswith("/") or re.match(r"^[a-zA-Z]:", v):
                raise ValueError(f"ground_truth_path debe ser una ruta relativa POSIX sin backslashes: '{v}'")
        return v


class BenchmarkManifest(BaseModel):
    """Manifiesto estructurado y versionado de un dataset de benchmark."""
    schema_version: int = Field(default=1, ge=1)
    dataset_id: str = Field(..., min_length=1, description="Identificador del dataset")
    dataset_version: str = Field(..., min_length=1, description="Versión semántica del dataset")
    description: Optional[str] = None
    created_at: str = Field(..., min_length=1, description="Marca de tiempo canónica de creación del dataset")
    pipelines: List[PipelineConfig] = Field(default_factory=list)
    cases: List[BenchmarkCase] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_manifest_integrity(self) -> "BenchmarkManifest":
        if not self.pipelines:
            raise ValueError("El manifiesto debe definir al menos un pipeline")

        case_ids = set()
        for c in self.cases:
            if c.case_id in case_ids:
                raise ValueError(f"Identificador de caso duplicado en manifiesto: '{c.case_id}'")
            case_ids.add(c.case_id)

            if c.evaluate_structured and not c.ground_truth_path:
                raise ValueError(f"El caso evaluable '{c.case_id}' requiere 'ground_truth_path'")

        pipe_ids = set()
        for p in self.pipelines:
            if p.pipeline_id in pipe_ids:
                raise ValueError(f"Identificador de pipeline duplicado en manifiesto: '{p.pipeline_id}'")
            pipe_ids.add(p.pipeline_id)

        for c in self.cases:
            for pred_pipe in c.predictions.keys():
                if pred_pipe not in pipe_ids:
                    raise ValueError(
                        f"El caso '{c.case_id}' contiene una clave de predicción para un pipeline no registrado en el manifiesto: '{pred_pipe}'"
                    )

        return self


class GroundTruthDocument(BaseModel):
    """Documento de verdad de referencia manual estructurado y versionado."""
    schema_version: int = Field(default=1, ge=1)
    case_id: str = Field(..., min_length=1)
    document: EstadilloDocument = Field(..., description="Documento canónico de verdad de referencia")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", strict=True)


class BenchmarkPrediction(BaseModel):
    """Predicción generada por un pipeline estructurada y versionada."""
    schema_version: int = Field(default=1, ge=1)
    case_id: str = Field(..., min_length=1)
    pipeline_id: str = Field(..., min_length=1)
    document: EstadilloDocument = Field(..., description="Documento canónico predicho por el pipeline")
    raw_ocr: Optional[str] = None
    execution_time_seconds: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", strict=True)


class BenchmarkReport(BaseModel):
    """Informe agregado determinista del benchmark."""
    schema_version: int = Field(default=1, ge=1)
    dataset_id: str = Field(..., min_length=1)
    dataset_version: str = Field(..., min_length=1)
    generated_at: str = Field(..., min_length=1, description="Marca de tiempo ISO o fija para determinismo")
    pipelines: List[PipelineConfig] = Field(default_factory=list)
    case_results: List[CaseMetrics] = Field(default_factory=list)
    category_aggregations: List[AggregatedMetrics] = Field(default_factory=list)
    global_aggregations: List[AggregatedMetrics] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", strict=True)
