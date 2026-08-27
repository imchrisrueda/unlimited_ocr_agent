import math
from typing import Optional, Literal, Union, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator

from .warnings import ExtractionWarning

DiagramType = Literal["field_sketch", "flowchart", "gps_sketch"]
OrientationType = Literal[
    "north_up",
    "south_up",
    "east_up",
    "west_up",
    "rotated",
    "unknown",
    "none",
]


class Point2D(BaseModel):
    """Punto visual 2D en coordenadas relativas normalizadas [0.0, 1.0].

    IMPORTANTE: Representa exclusivamente la posición gráfica dentro del dibujo/croquis,
    donde (0.0, 0.0) es la esquina superior izquierda y (1.0, 1.0) la inferior derecha.
    JAMÁS representa coordenadas GPS ni geográficas.
    """
    x: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Posición horizontal relativa en el dibujo [0.0, 1.0]. Nunca es GPS.",
    )
    y: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Posición vertical relativa en el dibujo [0.0, 1.0]. Nunca es GPS.",
    )

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_finite(self) -> "Point2D":
        if not (math.isfinite(self.x) and math.isfinite(self.y)):
            raise ValueError("Las coordenadas Point2D deben ser números finitos (no NaN ni Inf).")
        return self


class BoundingBox2D(BaseModel):
    """Caja delimitadora 2D en coordenadas visuales normalizadas [0.0, 1.0].

    Garantiza no degeneración estricta (x_min < x_max, y_min < y_max).
    """
    x_min: float = Field(..., ge=0.0, le=1.0, description="Borde izquierdo relativo [0.0, 1.0]")
    y_min: float = Field(..., ge=0.0, le=1.0, description="Borde superior relativo [0.0, 1.0]")
    x_max: float = Field(..., ge=0.0, le=1.0, description="Borde derecho relativo [0.0, 1.0]")
    y_max: float = Field(..., ge=0.0, le=1.0, description="Borde inferior relativo [0.0, 1.0]")

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_bounds_and_order(self) -> "BoundingBox2D":
        for val, name in [
            (self.x_min, "x_min"),
            (self.y_min, "y_min"),
            (self.x_max, "x_max"),
            (self.y_max, "y_max"),
        ]:
            if not math.isfinite(val):
                raise ValueError(f"El campo '{name}' de BoundingBox2D debe ser un número finito (no NaN ni Inf).")

        if self.x_min >= self.x_max:
            raise ValueError(f"BoundingBox2D degenerado: x_min ({self.x_min}) debe ser estrictamente menor que x_max ({self.x_max}).")
        if self.y_min >= self.y_max:
            raise ValueError(f"BoundingBox2D degenerado: y_min ({self.y_min}) debe ser estrictamente menor que y_max ({self.y_max}).")
        return self


class GeographicCoordinate(BaseModel):
    """Coordenada geográfica real leída explícitamente del documento o anotaciones.

    Completamente separada de las coordenadas visuales relativas de dibujo.
    Solo puede existir cuando hay latitud/longitud explícitas y legibles en el documento.
    Requiere obligatoriamente evidencia textual explícita ('raw_text' no vacío).
    """
    id: str = Field(..., min_length=1, description="Identificador único de la coordenada geográfica")
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitud en grados decimales [-90.0, 90.0]")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitud en grados decimales [-180.0, 180.0]")
    raw_text: str = Field(..., min_length=1, description="Texto literal observado en el documento (obligatorio)")
    source_page: int = Field(..., ge=1, description="Página fuente donde figura la coordenada")
    elevation_m: Optional[float] = Field(default=None, description="Elevación observada en metros sobre el nivel del mar")
    associated_point_id: Optional[str] = Field(default=None, description="ID del PointEntity visual asociado en el dibujo si existe")
    uncertain: bool = False
    alternatives: list[str] = Field(default_factory=list, description="Lecturas alternativas visualmente plausibles")
    description: Optional[str] = Field(default=None, description="Descripción textual o topónimo asociado")

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text_non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("raw_text en GeographicCoordinate es obligatorio y no puede estar vacío ni contener solo espacios.")
        return v.strip()

    @model_validator(mode="after")
    def validate_geographic_finite(self) -> "GeographicCoordinate":
        if not (math.isfinite(self.latitude) and math.isfinite(self.longitude)):
            raise ValueError("Latitud y longitud deben ser números finitos (no NaN ni Inf).")
        if self.elevation_m is not None and not math.isfinite(self.elevation_m):
            raise ValueError("La elevación debe ser un número finito (no NaN ni Inf).")
        if not self.uncertain and self.alternatives:
            raise ValueError("No se permiten 'alternatives' en GeographicCoordinate cuando uncertain=False.")
        return self


class DiagramOrientation(BaseModel):
    """Orientación explícita o declarada del diagrama/croquis.

    Reglas estrictas de orientación:
    - 'unknown' y 'none' indican ausencia de orientación declarada; no deben llevar 'degrees'.
    - Si la dirección es distinta de 'unknown' o 'none', se exige 'raw_text' no vacío como evidencia.
    - 'degrees' sólo se permite cuando direction='rotated'; en caso contrario debe ser None.
    - direction='rotated' exige obligatoriamente 'degrees' explícito y finito en [0.0, 360.0].
    """
    direction: OrientationType = Field(default="unknown", description="Orientación declarada o 'unknown' si no está indicada")
    degrees: Optional[float] = Field(default=None, ge=0.0, le=360.0, description="Ángulo explícito en grados (solo para 'rotated')")
    raw_text: Optional[str] = Field(default=None, description="Texto o símbolo de orientación observado (obligatorio si declarada)")
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_orientation_rules(self) -> "DiagramOrientation":
        # 1. Validación de grados finitos si existen
        if self.degrees is not None and not math.isfinite(self.degrees):
            raise ValueError("Los grados de orientación deben ser un número finito (no NaN ni Inf).")

        # 2. Orientación declarada (distinta de unknown/none) exige raw_text como evidencia visual
        if self.direction not in ("unknown", "none"):
            if not self.raw_text or not self.raw_text.strip():
                raise ValueError(
                    f"Orientation direction='{self.direction}' exige evidencia textual explícita en 'raw_text' "
                    f"(no se permite inferir o inventar la orientación)."
                )

        # 3. Reglas de degrees
        if self.direction == "rotated":
            if self.degrees is None:
                raise ValueError("Orientation direction='rotated' exige especificar el campo 'degrees' numérico explícito.")
        else:
            if self.degrees is not None:
                raise ValueError(
                    f"El campo 'degrees' solo se permite cuando direction='rotated', "
                    f"pero se recibió direction='{self.direction}' con degrees={self.degrees}."
                )

        return self


class PointEntity(BaseModel):
    """Entidad puntual en el dibujo (ej. vértice, hito, muestra, nodo, árbol, punto de inicio)."""
    id: str = Field(..., min_length=1, description="Identificador único del punto en el diagrama")
    coordinate: Point2D = Field(..., description="Posición visual relativa [0.0, 1.0] en el dibujo")
    label: Optional[str] = Field(default=None, description="Etiqueta visual asociada al punto")
    point_type: Optional[str] = Field(
        default=None,
        description="Tipo o rol conservador (ej: 'sample_point', 'tree', 'vertex', 'landmark', 'station', 'node')",
    )
    raw_text: Optional[str] = Field(default=None, description="Texto literal asociado observado en el documento")
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)


class LineEntity(BaseModel):
    """Entidad lineal o polilínea en el dibujo (ej. límite de parcela, transecto, arroyo, conector, flecha)."""
    id: str = Field(..., min_length=1, description="Identificador único de la línea en el diagrama")
    points: list[Point2D] = Field(
        ...,
        min_length=2,
        description="Secuencia de puntos visuales relativos [0.0, 1.0] que definen la línea o polilínea",
    )
    line_type: Optional[str] = Field(
        default=None,
        description="Tipo o rol de la línea (ej: 'boundary', 'transect', 'path', 'stream', 'flow_arrow', 'connector')",
    )
    directed: bool = Field(default=False, description="Indica si la línea tiene punta de flecha o dirección de flujo")
    source_point_id: Optional[str] = Field(default=None, description="ID de PointEntity de origen si conecta dos puntos")
    target_point_id: Optional[str] = Field(default=None, description="ID de PointEntity de destino si conecta dos puntos")
    label: Optional[str] = Field(default=None, description="Etiqueta visual asociada a la línea")
    raw_text: Optional[str] = Field(default=None, description="Texto literal asociado a la línea")
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_non_degenerate_line(self) -> "LineEntity":
        if len(self.points) < 2:
            raise ValueError(f"LineEntity '{self.id}' debe tener al menos 2 puntos visuales.")
        first = self.points[0]
        if all(p.x == first.x and p.y == first.y for p in self.points):
            raise ValueError(f"LineEntity '{self.id}' degenerada: todos los puntos de la línea son idénticos.")
        return self


def calculate_polygon_area(points: list[Point2D]) -> float:
    """Calcula el área absoluta de un polígono 2D mediante la fórmula de Gauss (Shoelace)."""
    n = len(points)
    if n < 3:
        return 0.0
    area2 = 0.0
    for i in range(n):
        j = (i + 1) % n
        area2 += points[i].x * points[j].y - points[j].x * points[i].y
    return abs(area2) / 2.0


class AreaEntity(BaseModel):
    """Entidad zonal o poligonal en el dibujo (ej. subparcela, bancal, cobertura, bloque, proceso)."""
    id: str = Field(..., min_length=1, description="Identificador único del área en el diagrama")
    bbox: Optional[BoundingBox2D] = Field(default=None, description="Bounding box visual relativo si es rectangular")
    polygon: Optional[list[Point2D]] = Field(
        default=None,
        min_length=3,
        description="Vértices visuales relativos si el área está delimitada por un polígono cerrado",
    )
    area_type: Optional[str] = Field(
        default=None,
        description="Tipo o rol de área (ej: 'plot', 'subdivision', 'buffer', 'canopy', 'zone', 'process_step')",
    )
    label: Optional[str] = Field(default=None, description="Etiqueta visual del área")
    raw_text: Optional[str] = Field(default=None, description="Texto literal contenido o asociado al área")
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_area_geometry(self) -> "AreaEntity":
        if self.bbox is None and self.polygon is None:
            raise ValueError(f"AreaEntity '{self.id}' debe especificar al menos 'bbox' o 'polygon'.")
        if self.polygon is not None:
            if len(self.polygon) < 3:
                raise ValueError(f"AreaEntity '{self.id}' con polígono debe tener al menos 3 vértices.")
            area = calculate_polygon_area(self.polygon)
            if area < 1e-7:
                raise ValueError(
                    f"AreaEntity '{self.id}' con polígono degenerado: el área del polígono es cero "
                    f"o los vértices son colineales (área={area:.8f})."
                )
        return self


class LabelEntity(BaseModel):
    """Texto o anotación textual explícita presente en el diagrama."""
    id: str = Field(..., min_length=1, description="Identificador único de la etiqueta en el diagrama")
    text: str = Field(..., min_length=1, description="Contenido textual legible de la etiqueta")
    position: Optional[Point2D] = Field(default=None, description="Posición visual relativa de anclaje de la etiqueta")
    attached_to_id: Optional[str] = Field(default=None, description="ID de entidad (Point/Line/Area/Relation) asociada si aplica")
    raw_text: Optional[str] = Field(default=None, description="Texto literal observado")
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)


class RelationEntity(BaseModel):
    """Relación topológica, de flujo o jerárquica explícita entre entidades del diagrama."""
    id: str = Field(..., min_length=1, description="Identificador único de la relación")
    source_id: str = Field(..., min_length=1, description="ID de la entidad de origen")
    target_id: str = Field(..., min_length=1, description="ID de la entidad de destino")
    relation_type: str = Field(
        ...,
        min_length=1,
        description="Tipo de relación (ej: 'flows_to', 'adjacent_to', 'contains', 'connected_to', 'measures', 'labels')",
    )
    directed: bool = Field(default=True, description="Indica si la relación es unidireccional")
    label: Optional[str] = Field(default=None, description="Texto o etiqueta asociada a la relación")
    raw_text: Optional[str] = Field(default=None, description="Texto literal asociado observado")
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)


class DiagramIR(BaseModel):
    """Representación Intermedia canónica, estructurada y auditable de un diagrama.

    Soporta exactamente diagram_type en {"field_sketch", "flowchart", "gps_sketch"}.
    Mantiene separación absoluta entre coordenadas visuales del dibujo [0,1] y coordenadas
    geográficas GPS observadas.
    """
    schema_version: Literal[1] = Field(default=1, description="Versión del schema de DiagramIR (estrictamente 1)")
    diagram_type: DiagramType = Field(
        ...,
        description="Tipo de diagrama: 'field_sketch' (croquis de campo), 'flowchart' (diagrama de flujo) o 'gps_sketch' (esquema GPS)",
    )
    source_page: int = Field(..., ge=1, description="Número de página fuente del documento (>= 1)")
    title: Optional[str] = Field(default=None, description="Título explícito del diagrama si figura en el documento")
    description: Optional[str] = Field(default=None, description="Descripción textual breve y objetiva del contenido visible")
    orientation: Optional[DiagramOrientation] = Field(default=None, description="Orientación del diagrama si está declarada")
    georeferenced: bool = Field(
        default=False,
        description="true ÚNICAMENTE si existen coordenadas geográficas reales explícitamente legibles en el documento",
    )
    crs: Optional[str] = Field(
        default=None,
        description="Sistema de Referencia de Coordenadas explícito (ej: 'EPSG:4326', 'ETRS89 / UTM zone 30N'). NUNCA tiene default.",
    )
    geographic_coordinates: list[GeographicCoordinate] = Field(
        default_factory=list,
        description="Coordenadas geográficas explícitamente observadas con procedencia obligatoria",
    )
    areas: list[AreaEntity] = Field(default_factory=list, description="Áreas, zonas o bloques visibles")
    points: list[PointEntity] = Field(default_factory=list, description="Puntos, nodos, vértices o hitos visibles")
    lines: list[LineEntity] = Field(default_factory=list, description="Líneas, límites, arroyos o flechas de conexión")
    labels: list[LabelEntity] = Field(default_factory=list, description="Etiquetas y textos legibles en el dibujo")
    relations: list[RelationEntity] = Field(default_factory=list, description="Relaciones explícitas de conectividad o adyacencia")
    raw_text: Optional[str] = Field(default=None, description="Transcripción del texto asociado al diagrama")
    warnings: list[ExtractionWarning] = Field(default_factory=list, description="Advertencias o incidencias detectadas")
    uncertain: bool = Field(default=False, description="Marca de incertidumbre general en la extracción del diagrama")

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_diagram_invariants(self) -> "DiagramIR":
        # 1. Georeferenced invariants
        if not self.georeferenced:
            if self.crs is not None:
                raise ValueError("georeferenced=False implica ausencia de CRS (crs debe ser None).")
            if self.geographic_coordinates:
                raise ValueError(
                    "georeferenced=False no puede contener coordenadas geográficas en 'geographic_coordinates'."
                )
        else:
            if not self.crs or not self.crs.strip():
                raise ValueError(
                    "georeferenced=True exige un CRS explícito y no vacío (ej: 'EPSG:4326', 'ETRS89 / UTM 30N'). No se asume por defecto."
                )
            if not self.geographic_coordinates:
                raise ValueError(
                    "georeferenced=True exige al menos una coordenada geográfica observada en 'geographic_coordinates'."
                )

        # 2. Source page consistency on geographic coordinates and warnings
        for idx, gc in enumerate(self.geographic_coordinates):
            if gc.source_page != self.source_page:
                raise ValueError(
                    f"Inconsistencia de source_page en geographic_coordinates[{idx}] ('{gc.id}'): "
                    f"esperado={self.source_page}, recibido={gc.source_page}"
                )

        for idx, w in enumerate(self.warnings):
            if w.source_page is not None and w.source_page != self.source_page:
                raise ValueError(
                    f"Inconsistencia de source_page en warnings[{idx}]: "
                    f"esperado={self.source_page}, recibido={w.source_page}"
                )

        # 3. Unique IDs across all entities
        seen_ids: dict[str, str] = {}
        for entity_collection, entity_type in [
            (self.geographic_coordinates, "GeographicCoordinate"),
            (self.areas, "AreaEntity"),
            (self.points, "PointEntity"),
            (self.lines, "LineEntity"),
            (self.labels, "LabelEntity"),
            (self.relations, "RelationEntity"),
        ]:
            for entity in entity_collection:
                ent_id = entity.id
                if ent_id in seen_ids:
                    raise ValueError(
                        f"ID duplicado '{ent_id}' detectado en {entity_type}. "
                        f"Ya fue utilizado previamente por una entidad de tipo {seen_ids[ent_id]}."
                    )
                seen_ids[ent_id] = entity_type

        # 4. Reference integrity check
        for rel in self.relations:
            if rel.source_id not in seen_ids:
                raise ValueError(
                    f"RelationEntity '{rel.id}' referencia source_id='{rel.source_id}' que no existe en el diagrama."
                )
            if rel.target_id not in seen_ids:
                raise ValueError(
                    f"RelationEntity '{rel.id}' referencia target_id='{rel.target_id}' que no existe en el diagrama."
                )

        for line in self.lines:
            if line.source_point_id is not None:
                if line.source_point_id not in seen_ids:
                    raise ValueError(
                        f"LineEntity '{line.id}' referencia source_point_id='{line.source_point_id}' que no existe en el diagrama."
                    )
                if seen_ids[line.source_point_id] != "PointEntity":
                    raise ValueError(
                        f"LineEntity '{line.id}' referencia source_point_id='{line.source_point_id}' "
                        f"que es de tipo '{seen_ids[line.source_point_id]}', pero debe ser 'PointEntity'."
                    )

            if line.target_point_id is not None:
                if line.target_point_id not in seen_ids:
                    raise ValueError(
                        f"LineEntity '{line.id}' referencia target_point_id='{line.target_point_id}' que no existe en el diagrama."
                    )
                if seen_ids[line.target_point_id] != "PointEntity":
                    raise ValueError(
                        f"LineEntity '{line.id}' referencia target_point_id='{line.target_point_id}' "
                        f"que es de tipo '{seen_ids[line.target_point_id]}', pero debe ser 'PointEntity'."
                    )

        for label in self.labels:
            if label.attached_to_id is not None and label.attached_to_id not in seen_ids:
                raise ValueError(
                    f"LabelEntity '{label.id}' referencia attached_to_id='{label.attached_to_id}' que no existe en el diagrama."
                )

        for gc in self.geographic_coordinates:
            if gc.associated_point_id is not None:
                if gc.associated_point_id not in seen_ids:
                    raise ValueError(
                        f"GeographicCoordinate '{gc.id}' referencia associated_point_id='{gc.associated_point_id}' "
                        f"que no existe en el diagrama."
                    )
                if seen_ids[gc.associated_point_id] != "PointEntity":
                    raise ValueError(
                        f"GeographicCoordinate '{gc.id}' referencia associated_point_id='{gc.associated_point_id}' "
                        f"que es de tipo '{seen_ids[gc.associated_point_id]}', pero debe ser 'PointEntity'."
                    )

        return self


# =====================================================================
# DTO Models for compact VLM Structured Output
# =====================================================================

class Point2DDTO(BaseModel):
    """Punto visual 2D relativo en DTO [0.0, 1.0]."""
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    model_config = ConfigDict(extra="forbid", strict=True)


class BoundingBox2DDTO(BaseModel):
    """Caja visual 2D en DTO [0.0, 1.0]."""
    x_min: float = Field(..., ge=0.0, le=1.0)
    y_min: float = Field(..., ge=0.0, le=1.0)
    x_max: float = Field(..., ge=0.0, le=1.0)
    y_max: float = Field(..., ge=0.0, le=1.0)
    model_config = ConfigDict(extra="forbid", strict=True)


class GeographicCoordinateDTO(BaseModel):
    """DTO compacto para coordenadas geográficas explícitas."""
    id: str = Field(..., min_length=1)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    raw_text: str = Field(..., min_length=1)
    elevation_m: Optional[float] = None
    associated_point_id: Optional[str] = None
    uncertain: bool = False
    alternatives: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    model_config = ConfigDict(extra="forbid", strict=True)


class DiagramOrientationDTO(BaseModel):
    """DTO compacto de orientación."""
    direction: OrientationType = "unknown"
    degrees: Optional[float] = Field(default=None, ge=0.0, le=360.0)
    raw_text: Optional[str] = None
    uncertain: bool = False
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_dto_orientation_rules(self) -> "DiagramOrientationDTO":
        if self.direction not in ("unknown", "none"):
            if not self.raw_text or not self.raw_text.strip():
                raise ValueError(
                    f"Orientation direction='{self.direction}' exige evidencia textual explícita en 'raw_text'."
                )
        if self.direction == "rotated":
            if self.degrees is None:
                raise ValueError("Orientation direction='rotated' exige especificar 'degrees'.")
        else:
            if self.degrees is not None:
                raise ValueError(f"'degrees' solo se permite cuando direction='rotated', no para '{self.direction}'.")
        return self


class PointEntityDTO(BaseModel):
    """DTO compacto de punto visual."""
    id: str = Field(..., min_length=1)
    coordinate: Point2DDTO
    label: Optional[str] = None
    point_type: Optional[str] = None
    raw_text: Optional[str] = None
    uncertain: bool = False
    model_config = ConfigDict(extra="forbid", strict=True)


class LineEntityDTO(BaseModel):
    """DTO compacto de línea o polilínea."""
    id: str = Field(..., min_length=1)
    points: list[Point2DDTO] = Field(..., min_length=2)
    line_type: Optional[str] = None
    directed: bool = False
    source_point_id: Optional[str] = None
    target_point_id: Optional[str] = None
    label: Optional[str] = None
    raw_text: Optional[str] = None
    uncertain: bool = False
    model_config = ConfigDict(extra="forbid", strict=True)


class AreaEntityDTO(BaseModel):
    """DTO compacto de área o polígono."""
    id: str = Field(..., min_length=1)
    bbox: Optional[BoundingBox2DDTO] = None
    polygon: Optional[list[Point2DDTO]] = Field(default=None, min_length=3)
    area_type: Optional[str] = None
    label: Optional[str] = None
    raw_text: Optional[str] = None
    uncertain: bool = False
    model_config = ConfigDict(extra="forbid", strict=True)


class LabelEntityDTO(BaseModel):
    """DTO compacto de etiqueta."""
    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    position: Optional[Point2DDTO] = None
    attached_to_id: Optional[str] = None
    raw_text: Optional[str] = None
    uncertain: bool = False
    model_config = ConfigDict(extra="forbid", strict=True)


class RelationEntityDTO(BaseModel):
    """DTO compacto de relación."""
    id: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    relation_type: str = Field(..., min_length=1)
    directed: bool = True
    label: Optional[str] = None
    raw_text: Optional[str] = None
    uncertain: bool = False
    model_config = ConfigDict(extra="forbid", strict=True)


class DiagramDTO(BaseModel):
    """Contrato de inferencia estructurada compacto para extracción de diagramas mediante VLM."""
    schema_version: Literal[1] = 1
    diagram_type: DiagramType
    source_page: int = Field(..., ge=1)
    title: Optional[str] = None
    description: Optional[str] = None
    orientation: Optional[DiagramOrientationDTO] = None
    georeferenced: bool = False
    crs: Optional[str] = None
    geographic_coordinates: list[GeographicCoordinateDTO] = Field(default_factory=list)
    areas: list[AreaEntityDTO] = Field(default_factory=list)
    points: list[PointEntityDTO] = Field(default_factory=list)
    lines: list[LineEntityDTO] = Field(default_factory=list)
    labels: list[LabelEntityDTO] = Field(default_factory=list)
    relations: list[RelationEntityDTO] = Field(default_factory=list)
    raw_text: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    uncertain: bool = False

    model_config = ConfigDict(extra="forbid", strict=True)


def dto_to_diagram_ir(
    dto: Union[DiagramDTO, DiagramIR],
    source_page: Optional[int] = None,
) -> DiagramIR:
    """Convierte de forma pura y determinista un DiagramDTO al modelo de dominio canónico DiagramIR.

    Garantiza:
    - No muta el DTO de entrada.
    - Si se recibe un DiagramIR, devuelve una copia profunda (`model_copy(deep=True)`), sin compartir referencias mutables.
    - Rechaza explícitamente cualquier discrepancia entre `dto.source_page` y el `source_page` esperado.
    - Rechaza referencias rotas, IDs duplicados o datos inválidos sin reparaciones silenciosas ni inventar semántica.
    """
    if isinstance(dto, DiagramIR):
        if source_page is not None and dto.source_page != source_page:
            raise ValueError(
                f"Discrepancia de source_page en DiagramIR: recibido={dto.source_page}, esperado={source_page}"
            )
        copied = dto.model_copy(deep=True)
        # Validar invariantes en el objeto copiado
        validate_diagram_invariants = getattr(copied, "validate_diagram_invariants", None)
        if callable(validate_diagram_invariants):
            copied.validate_diagram_invariants()
        return copied

    effective_page = dto.source_page
    if source_page is not None and dto.source_page != source_page:
        raise ValueError(
            f"Discrepancia de source_page en DiagramDTO: recibido={dto.source_page}, esperado={source_page}"
        )

    # 1. Orientation
    canonical_orientation: Optional[DiagramOrientation] = None
    if dto.orientation is not None:
        canonical_orientation = DiagramOrientation(
            direction=dto.orientation.direction,
            degrees=dto.orientation.degrees,
            raw_text=dto.orientation.raw_text,
            uncertain=dto.orientation.uncertain,
        )

    # 2. Geographic coordinates
    canonical_geo: list[GeographicCoordinate] = []
    for gc in dto.geographic_coordinates:
        canonical_geo.append(
            GeographicCoordinate(
                id=gc.id,
                latitude=gc.latitude,
                longitude=gc.longitude,
                raw_text=gc.raw_text,
                source_page=effective_page,
                elevation_m=gc.elevation_m,
                associated_point_id=gc.associated_point_id,
                uncertain=gc.uncertain,
                alternatives=list(gc.alternatives),
                description=gc.description,
            )
        )

    # 3. Areas
    canonical_areas: list[AreaEntity] = []
    for a in dto.areas:
        bbox = (
            BoundingBox2D(
                x_min=a.bbox.x_min,
                y_min=a.bbox.y_min,
                x_max=a.bbox.x_max,
                y_max=a.bbox.y_max,
            )
            if a.bbox is not None
            else None
        )
        polygon = (
            [Point2D(x=p.x, y=p.y) for p in a.polygon]
            if a.polygon is not None
            else None
        )
        canonical_areas.append(
            AreaEntity(
                id=a.id,
                bbox=bbox,
                polygon=polygon,
                area_type=a.area_type,
                label=a.label,
                raw_text=a.raw_text,
                uncertain=a.uncertain,
            )
        )

    # 4. Points
    canonical_points: list[PointEntity] = []
    for p in dto.points:
        canonical_points.append(
            PointEntity(
                id=p.id,
                coordinate=Point2D(x=p.coordinate.x, y=p.coordinate.y),
                label=p.label,
                point_type=p.point_type,
                raw_text=p.raw_text,
                uncertain=p.uncertain,
            )
        )

    # 5. Lines
    canonical_lines: list[LineEntity] = []
    for l in dto.lines:
        pts = [Point2D(x=p.x, y=p.y) for p in l.points]
        canonical_lines.append(
            LineEntity(
                id=l.id,
                points=pts,
                line_type=l.line_type,
                directed=l.directed,
                source_point_id=l.source_point_id,
                target_point_id=l.target_point_id,
                label=l.label,
                raw_text=l.raw_text,
                uncertain=l.uncertain,
            )
        )

    # 6. Labels
    canonical_labels: list[LabelEntity] = []
    for lbl in dto.labels:
        pos = (
            Point2D(x=lbl.position.x, y=lbl.position.y)
            if lbl.position is not None
            else None
        )
        canonical_labels.append(
            LabelEntity(
                id=lbl.id,
                text=lbl.text,
                position=pos,
                attached_to_id=lbl.attached_to_id,
                raw_text=lbl.raw_text,
                uncertain=lbl.uncertain,
            )
        )

    # 7. Relations
    canonical_relations: list[RelationEntity] = []
    for r in dto.relations:
        canonical_relations.append(
            RelationEntity(
                id=r.id,
                source_id=r.source_id,
                target_id=r.target_id,
                relation_type=r.relation_type,
                directed=r.directed,
                label=r.label,
                raw_text=r.raw_text,
                uncertain=r.uncertain,
            )
        )

    # 8. Warnings
    canonical_warnings = [
        ExtractionWarning(code="VLM_WARNING", message=str(w), source_page=effective_page)
        for w in (dto.warnings or [])
        if str(w).strip()
    ]

    return DiagramIR(
        schema_version=1,
        diagram_type=dto.diagram_type,
        source_page=effective_page,
        title=dto.title,
        description=dto.description,
        orientation=canonical_orientation,
        georeferenced=dto.georeferenced,
        crs=dto.crs,
        geographic_coordinates=canonical_geo,
        areas=canonical_areas,
        points=canonical_points,
        lines=canonical_lines,
        labels=canonical_labels,
        relations=canonical_relations,
        raw_text=dto.raw_text,
        warnings=canonical_warnings,
        uncertain=dto.uncertain,
    )
