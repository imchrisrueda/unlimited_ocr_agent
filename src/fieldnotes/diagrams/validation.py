import math
from typing import Optional, List, Dict, Set, Tuple
from ..schemas.diagram import (
    DiagramIR,
    Point2D,
    BoundingBox2D,
    GeographicCoordinate,
    calculate_polygon_area,
)
from ..schemas.warnings import ExtractionWarning


class DiagramValidationError(ValueError):
    """Excepción base para errores de validación en DiagramIR."""
    pass


class DiagramDuplicateIdError(DiagramValidationError):
    """Excepción para identificadores duplicados entre entidades del diagrama."""
    pass


class DiagramReferenceError(DiagramValidationError):
    """Excepción para referencias internas rotas o no resolubles en el diagrama."""
    pass


class DiagramGeoreferenceError(DiagramValidationError):
    """Excepción para violaciones de invariantes de georreferenciación o CRS."""
    pass


class DiagramGeometryError(DiagramValidationError):
    """Excepción para geometrías visuales inválidas o degeneradas."""
    pass


def validate_diagram_ir(
    diagram: DiagramIR,
    raise_on_error: bool = True,
) -> list[ExtractionWarning]:
    """Valida de forma pura, determinista y exhaustiva las invariantes de DiagramIR.

    Verificaciones realizadas:
    1. schema_version estrictamente 1 y diagram_type estrictamente en {"field_sketch", "flowchart", "gps_sketch"}.
    2. Coordenadas visuales normalizadas finitas en [0.0, 1.0] (x/y son relativas al dibujo, NUNCA GPS).
    3. Geometrías no degeneradas:
       - BoundingBox2D no degenerado (x_min < x_max, y_min < y_max).
       - Polígonos de áreas con área absoluta > 1e-7 (no colineales ni de área cero).
       - Líneas con al menos 2 puntos no idénticos.
    4. Separación absoluta entre coordenadas visuales y geográficas:
       - georeferenced=False => crs=None y geographic_coordinates=[].
       - georeferenced=True => crs no nulo ni vacío (sin default) y al menos una coordenada geográfica con provenance y raw_text obligatorio.
    5. Orientación estricta:
       - Dirección declarada (distinta de unknown/none) exige 'raw_text' no vacío.
       - 'degrees' solo se permite cuando direction='rotated' (y es obligatorio en ese caso).
       - 'unknown' y 'none' exigen degrees=None.
    6. Unicidad global de IDs en todo el diagrama.
    7. Integridad de referencias internas:
       - Relations: source_id y target_id existen.
       - LineEntity: source_point_id y target_point_id existen y son EXCLUSIVAMENTE 'PointEntity'.
       - LabelEntity: attached_to_id existe.
       - GeographicCoordinate: associated_point_id existe y es EXCLUSIVAMENTE 'PointEntity'.
    8. Consistencia de source_page en coordenadas geográficas y advertencias.

    Si raise_on_error=True, lanza DiagramValidationError (o subclase) ante cualquier violación estructural.
    Devuelve la lista determinista de advertencias asociadas.
    """
    if not isinstance(diagram, DiagramIR):
        err_msg = f"Se esperaba una instancia de DiagramIR, recibido: {type(diagram).__name__}"
        if raise_on_error:
            raise DiagramValidationError(err_msg)
        return [ExtractionWarning(code="INVALID_TYPE", message=err_msg)]

    warnings: list[ExtractionWarning] = list(diagram.warnings)

    # 1. Versión y tipo de diagrama
    if diagram.schema_version != 1:
        err_msg = f"schema_version '{diagram.schema_version}' inválido. Debe ser exactamente 1."
        if raise_on_error:
            raise DiagramValidationError(err_msg)
        warnings.append(ExtractionWarning(code="INVALID_SCHEMA_VERSION", message=err_msg, source_page=diagram.source_page))

    allowed_types = {"field_sketch", "flowchart", "gps_sketch"}
    if diagram.diagram_type not in allowed_types:
        err_msg = f"diagram_type '{diagram.diagram_type}' inválido. Debe ser uno de {allowed_types}."
        if raise_on_error:
            raise DiagramValidationError(err_msg)
        warnings.append(ExtractionWarning(code="INVALID_DIAGRAM_TYPE", message=err_msg, source_page=diagram.source_page))

    # 2. Source page
    if diagram.source_page < 1:
        err_msg = f"source_page debe ser >= 1, recibido: {diagram.source_page}"
        if raise_on_error:
            raise DiagramValidationError(err_msg)
        warnings.append(ExtractionWarning(code="INVALID_SOURCE_PAGE", message=err_msg))

    for idx, w in enumerate(diagram.warnings):
        if w.source_page is not None and w.source_page != diagram.source_page:
            err_msg = (
                f"Inconsistencia de source_page en advertencia {idx}: "
                f"esperado={diagram.source_page}, recibido={w.source_page}"
            )
            if raise_on_error:
                raise DiagramValidationError(err_msg)

    # 3. Invariantes de georreferenciación
    if not diagram.georeferenced:
        if diagram.crs is not None:
            err_msg = f"georeferenced=False exige crs=None, pero se encontró crs='{diagram.crs}'."
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="GEOREF_CRS_MISMATCH", message=err_msg, source_page=diagram.source_page))

        if diagram.geographic_coordinates:
            err_msg = (
                f"georeferenced=False no puede contener coordenadas geográficas "
                f"({len(diagram.geographic_coordinates)} encontradas)."
            )
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="GEOREF_COORDS_PRESENT", message=err_msg, source_page=diagram.source_page))
    else:
        if not diagram.crs or not diagram.crs.strip():
            err_msg = "georeferenced=True exige un CRS explícito (no nulo ni vacío). No se debe asumir EPSG:4326 por defecto."
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="MISSING_CRS", message=err_msg, source_page=diagram.source_page))

        if not diagram.geographic_coordinates:
            err_msg = "georeferenced=True exige al menos una coordenada geográfica observada en 'geographic_coordinates'."
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="EMPTY_GEO_COORDS", message=err_msg, source_page=diagram.source_page))

    # Validar coordenadas geográficas individuales
    for idx, gc in enumerate(diagram.geographic_coordinates):
        if gc.source_page != diagram.source_page:
            err_msg = (
                f"Inconsistencia de source_page en geographic_coordinates[{idx}] ('{gc.id}'): "
                f"esperado={diagram.source_page}, recibido={gc.source_page}"
            )
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="GEO_SOURCE_PAGE_MISMATCH", message=err_msg, source_page=diagram.source_page))

        if not gc.raw_text or not gc.raw_text.strip():
            err_msg = f"GeographicCoordinate '{gc.id}' carece de evidencia textual explícita en 'raw_text'."
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="MISSING_GEO_RAW_TEXT", message=err_msg, source_page=diagram.source_page))

        if not (-90.0 <= gc.latitude <= 90.0 and math.isfinite(gc.latitude)):
            err_msg = f"Latitud fuera de rango [-90, 90] o no finita en '{gc.id}': {gc.latitude}"
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="INVALID_LATITUDE", message=err_msg, source_page=diagram.source_page))

        if not (-180.0 <= gc.longitude <= 180.0 and math.isfinite(gc.longitude)):
            err_msg = f"Longitud fuera de rango [-180, 180] o no finita en '{gc.id}': {gc.longitude}"
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="INVALID_LONGITUDE", message=err_msg, source_page=diagram.source_page))

        if gc.elevation_m is not None and not math.isfinite(gc.elevation_m):
            err_msg = f"Elevación no finita en '{gc.id}': {gc.elevation_m}"
            if raise_on_error:
                raise DiagramGeoreferenceError(err_msg)
            warnings.append(ExtractionWarning(code="INVALID_ELEVATION", message=err_msg, source_page=diagram.source_page))

    # 4. Orientación estricta
    if diagram.orientation is not None:
        o = diagram.orientation
        if o.direction not in ("unknown", "none"):
            if not o.raw_text or not o.raw_text.strip():
                err_msg = f"Orientation direction='{o.direction}' exige evidencia textual explícita en 'raw_text'."
                if raise_on_error:
                    raise DiagramValidationError(err_msg)
                warnings.append(ExtractionWarning(code="MISSING_ORIENTATION_RAW_TEXT", message=err_msg, source_page=diagram.source_page))

        if o.direction == "rotated":
            if o.degrees is None or not (0.0 <= o.degrees <= 360.0 and math.isfinite(o.degrees)):
                err_msg = f"Orientation direction='rotated' exige 'degrees' explícito en [0, 360], recibido: {o.degrees}"
                if raise_on_error:
                    raise DiagramValidationError(err_msg)
                warnings.append(ExtractionWarning(code="INVALID_ROTATION_DEGREES", message=err_msg, source_page=diagram.source_page))
        else:
            if o.degrees is not None:
                err_msg = f"'degrees' solo se permite cuando direction='rotated', no para direction='{o.direction}'."
                if raise_on_error:
                    raise DiagramValidationError(err_msg)
                warnings.append(ExtractionWarning(code="UNEXPECTED_DEGREES", message=err_msg, source_page=diagram.source_page))

    # 5. Unicidad global de IDs
    seen_ids: dict[str, str] = {}
    collections: list[Tuple[list, str]] = [
        (diagram.geographic_coordinates, "GeographicCoordinate"),
        (diagram.areas, "AreaEntity"),
        (diagram.points, "PointEntity"),
        (diagram.lines, "LineEntity"),
        (diagram.labels, "LabelEntity"),
        (diagram.relations, "RelationEntity"),
    ]

    for entity_list, type_name in collections:
        for ent in entity_list:
            ent_id = ent.id
            if not ent_id or not ent_id.strip():
                err_msg = f"Entidad de tipo {type_name} contiene un 'id' vacío o en blanco."
                if raise_on_error:
                    raise DiagramValidationError(err_msg)
                warnings.append(ExtractionWarning(code="EMPTY_ENTITY_ID", message=err_msg, source_page=diagram.source_page))

            if ent_id in seen_ids:
                err_msg = (
                    f"ID duplicado '{ent_id}' en {type_name}. "
                    f"Ya fue utilizado previamente por una entidad de tipo {seen_ids[ent_id]}."
                )
                if raise_on_error:
                    raise DiagramDuplicateIdError(err_msg)
                warnings.append(ExtractionWarning(code="DUPLICATE_ENTITY_ID", message=err_msg, source_page=diagram.source_page))
            else:
                seen_ids[ent_id] = type_name

    # 6. Validación de geometrías visuales
    # Points
    for pt in diagram.points:
        if not (0.0 <= pt.coordinate.x <= 1.0 and math.isfinite(pt.coordinate.x)):
            err_msg = f"Coordenada visual x fuera de rango [0,1] o no finita en punto '{pt.id}': {pt.coordinate.x}"
            if raise_on_error:
                raise DiagramGeometryError(err_msg)
            warnings.append(ExtractionWarning(code="INVALID_VISUAL_X", message=err_msg, source_page=diagram.source_page))
        if not (0.0 <= pt.coordinate.y <= 1.0 and math.isfinite(pt.coordinate.y)):
            err_msg = f"Coordenada visual y fuera de rango [0,1] o no finita en punto '{pt.id}': {pt.coordinate.y}"
            if raise_on_error:
                raise DiagramGeometryError(err_msg)
            warnings.append(ExtractionWarning(code="INVALID_VISUAL_Y", message=err_msg, source_page=diagram.source_page))

    # Lines
    for line in diagram.lines:
        if len(line.points) < 2:
            err_msg = f"LineEntity '{line.id}' debe contener al menos 2 puntos visuales."
            if raise_on_error:
                raise DiagramGeometryError(err_msg)
            warnings.append(ExtractionWarning(code="DEGENERATE_LINE", message=err_msg, source_page=diagram.source_page))
        else:
            first = line.points[0]
            if all(p.x == first.x and p.y == first.y for p in line.points):
                err_msg = f"LineEntity '{line.id}' degenerada: todos los puntos son idénticos ({first.x}, {first.y})."
                if raise_on_error:
                    raise DiagramGeometryError(err_msg)
                warnings.append(ExtractionWarning(code="DEGENERATE_LINE", message=err_msg, source_page=diagram.source_page))

    # Areas
    for area in diagram.areas:
        if area.bbox is None and area.polygon is None:
            err_msg = f"AreaEntity '{area.id}' debe tener 'bbox' o 'polygon' definido."
            if raise_on_error:
                raise DiagramGeometryError(err_msg)
            warnings.append(ExtractionWarning(code="MISSING_AREA_GEOMETRY", message=err_msg, source_page=diagram.source_page))

        if area.bbox is not None:
            if area.bbox.x_min >= area.bbox.x_max or area.bbox.y_min >= area.bbox.y_max:
                err_msg = f"AreaEntity '{area.id}' tiene un BoundingBox2D degenerado."
                if raise_on_error:
                    raise DiagramGeometryError(err_msg)
                warnings.append(ExtractionWarning(code="DEGENERATE_BBOX", message=err_msg, source_page=diagram.source_page))

        if area.polygon is not None:
            if len(area.polygon) < 3:
                err_msg = f"AreaEntity '{area.id}' con polígono debe tener al menos 3 vértices."
                if raise_on_error:
                    raise DiagramGeometryError(err_msg)
                warnings.append(ExtractionWarning(code="DEGENERATE_POLYGON", message=err_msg, source_page=diagram.source_page))
            else:
                poly_area = calculate_polygon_area(area.polygon)
                if poly_area < 1e-7:
                    err_msg = (
                        f"AreaEntity '{area.id}' con polígono degenerado: el área del polígono es cero "
                        f"o los vértices son colineales (área={poly_area:.8f})."
                    )
                    if raise_on_error:
                        raise DiagramGeometryError(err_msg)
                    warnings.append(ExtractionWarning(code="DEGENERATE_POLYGON_AREA", message=err_msg, source_page=diagram.source_page))

    # 7. Integridad referencial
    for rel in diagram.relations:
        if rel.source_id not in seen_ids:
            err_msg = f"RelationEntity '{rel.id}' referencia source_id='{rel.source_id}' inexistente."
            if raise_on_error:
                raise DiagramReferenceError(err_msg)
            warnings.append(ExtractionWarning(code="BROKEN_RELATION_SOURCE", message=err_msg, source_page=diagram.source_page))

        if rel.target_id not in seen_ids:
            err_msg = f"RelationEntity '{rel.id}' referencia target_id='{rel.target_id}' inexistente."
            if raise_on_error:
                raise DiagramReferenceError(err_msg)
            warnings.append(ExtractionWarning(code="BROKEN_RELATION_TARGET", message=err_msg, source_page=diagram.source_page))

    for line in diagram.lines:
        if line.source_point_id is not None:
            if line.source_point_id not in seen_ids:
                err_msg = f"LineEntity '{line.id}' referencia source_point_id='{line.source_point_id}' inexistente."
                if raise_on_error:
                    raise DiagramReferenceError(err_msg)
                warnings.append(ExtractionWarning(code="BROKEN_LINE_SOURCE", message=err_msg, source_page=diagram.source_page))
            elif seen_ids[line.source_point_id] != "PointEntity":
                err_msg = (
                    f"LineEntity '{line.id}' referencia source_point_id='{line.source_point_id}' "
                    f"que es de tipo '{seen_ids[line.source_point_id]}', pero debe ser 'PointEntity'."
                )
                if raise_on_error:
                    raise DiagramReferenceError(err_msg)
                warnings.append(ExtractionWarning(code="INVALID_LINE_SOURCE_TYPE", message=err_msg, source_page=diagram.source_page))

        if line.target_point_id is not None:
            if line.target_point_id not in seen_ids:
                err_msg = f"LineEntity '{line.id}' referencia target_point_id='{line.target_point_id}' inexistente."
                if raise_on_error:
                    raise DiagramReferenceError(err_msg)
                warnings.append(ExtractionWarning(code="BROKEN_LINE_TARGET", message=err_msg, source_page=diagram.source_page))
            elif seen_ids[line.target_point_id] != "PointEntity":
                err_msg = (
                    f"LineEntity '{line.id}' referencia target_point_id='{line.target_point_id}' "
                    f"que es de tipo '{seen_ids[line.target_point_id]}', pero debe ser 'PointEntity'."
                )
                if raise_on_error:
                    raise DiagramReferenceError(err_msg)
                warnings.append(ExtractionWarning(code="INVALID_LINE_TARGET_TYPE", message=err_msg, source_page=diagram.source_page))

    for label in diagram.labels:
        if label.attached_to_id is not None and label.attached_to_id not in seen_ids:
            err_msg = f"LabelEntity '{label.id}' referencia attached_to_id='{label.attached_to_id}' inexistente."
            if raise_on_error:
                raise DiagramReferenceError(err_msg)
            warnings.append(ExtractionWarning(code="BROKEN_LABEL_ATTACHMENT", message=err_msg, source_page=diagram.source_page))

    for gc in diagram.geographic_coordinates:
        if gc.associated_point_id is not None:
            if gc.associated_point_id not in seen_ids:
                err_msg = f"GeographicCoordinate '{gc.id}' referencia associated_point_id='{gc.associated_point_id}' inexistente."
                if raise_on_error:
                    raise DiagramReferenceError(err_msg)
                warnings.append(ExtractionWarning(code="BROKEN_GEO_POINT_REF", message=err_msg, source_page=diagram.source_page))
            elif seen_ids[gc.associated_point_id] != "PointEntity":
                err_msg = (
                    f"GeographicCoordinate '{gc.id}' referencia associated_point_id='{gc.associated_point_id}' "
                    f"que es de tipo '{seen_ids[gc.associated_point_id]}', pero debe ser 'PointEntity'."
                )
                if raise_on_error:
                    raise DiagramReferenceError(err_msg)
                warnings.append(ExtractionWarning(code="INVALID_GEO_POINT_REF_TYPE", message=err_msg, source_page=diagram.source_page))

    return warnings
