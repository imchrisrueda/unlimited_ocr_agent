import os
import posixpath
import re
import tempfile
from pathlib import Path
from typing import Optional, Union, Tuple
from PIL import Image

from src.fieldnotes.schemas.review import ReviewIssue, NormalizedBBox
from src.fieldnotes.review.issues import match_region_for_issue


def validate_safe_crop_path(target_path: Union[str, Path], allowed_dir: Union[str, Path]) -> Path:
    """Verifica que target_path quede estrictamente confinado dentro de allowed_dir.

    Rechaza path traversal o escapes de directorio.
    """
    real_target = Path(target_path).resolve()
    real_allowed = Path(allowed_dir).resolve()
    try:
        real_target.relative_to(real_allowed)
    except ValueError as exc:
        raise ValueError(
            f"Ruta de crop insegura: target='{real_target}' escapa de '{real_allowed}'"
        ) from exc
    return real_target


def generate_crop_filename(
    page: Optional[int],
    field: str,
    row_key: Optional[str] = None,
    warning_code: Optional[str] = None,
    index: int = 0,
) -> str:
    """Genera un nombre de archivo seguro, determinista y único para un crop visual."""
    parts = [f"p{page:03d}" if page is not None else "doc"]
    if row_key:
        clean_row = re.sub(r"[^\w\-]", "_", str(row_key))
        parts.append(clean_row)
    clean_field = re.sub(r"[^\w\-]", "_", str(field))
    parts.append(clean_field)
    if warning_code and warning_code != "UNCERTAIN_EVIDENCE":
        clean_code = re.sub(r"[^\w\-]", "_", str(warning_code))
        parts.append(clean_code)
    if index > 0:
        parts.append(str(index))

    base_name = "_".join(p for p in parts if p)
    sanitized = re.sub(r"[^\w\-]", "_", base_name)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return f"{sanitized}.png"


def crop_image_region(
    image_path: Union[str, Path],
    bbox: NormalizedBBox,
    output_path: Union[str, Path],
    allowed_dir: Optional[Union[str, Path]] = None,
    min_size_px: int = 1,
) -> Path:
    """Recorta una región visual normalizada [0,1] desde una imagen canónica y la guarda como PNG.

    Verifica confinamiento, límites de imagen, tamaño mínimo y decodificación correcta.
    """
    in_path = Path(image_path).resolve()
    if not in_path.is_file():
        raise FileNotFoundError(f"Imagen fuente para crop no encontrada: '{in_path}'")

    out_path = Path(output_path).resolve()
    if allowed_dir is not None:
        validate_safe_crop_path(out_path, allowed_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(in_path) as img:
        img_w, img_h = img.size
        if img_w <= 0 or img_h <= 0:
            raise ValueError(f"Dimensiones de imagen inválidas: {img.size}")

        # Calcular coordenadas enteras en píxeles y clampear a dimensiones reales
        px0 = max(0, min(img_w, int(round(bbox.x0 * img_w))))
        py0 = max(0, min(img_h, int(round(bbox.y0 * img_h))))
        px1 = max(0, min(img_w, int(round(bbox.x1 * img_w))))
        py1 = max(0, min(img_h, int(round(bbox.y1 * img_h))))

        crop_w = px1 - px0
        crop_h = py1 - py0

        if crop_w < min_size_px or crop_h < min_size_px:
            raise ValueError(
                f"Región de crop degenerada o menor al tamaño mínimo ({min_size_px}px): "
                f"ancho={crop_w}px, alto={crop_h}px (bbox normalizado={bbox})"
            )

        cropped = img.crop((px0, py0, px1, py1))

        # Escritura atómica
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            prefix=".tmp_crop_",
            suffix=".png",
            dir=str(out_path.parent),
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_path_str)

        try:
            cropped.save(str(tmp_path), format="PNG")
            os.replace(str(tmp_path), str(out_path))
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

    # Verificación de decodificación del archivo generado
    with Image.open(out_path) as verified_img:
        verified_img.verify()

    return out_path


def generate_review_crops_for_issues(
    issues: list[ReviewIssue],
    page_images: dict[int, Path],
    regions_map: dict[tuple[int, Optional[str], Optional[str]], NormalizedBBox],
    review_dir: Path,
    canonical_base_dir: Path,
) -> list[ReviewIssue]:
    """Genera crops PNG para los ReviewIssues que tengan una región de evidencia válida asociada.

    Función no mutativa: devuelve copias de ReviewIssue y nunca altera la lista ni los objetos de entrada.
    Valida estrictamente que review_dir esté confinado dentro de canonical_base_dir antes de crear directorios o escribir.
    Solo la ausencia contractual de región o de imagen deja crop_path=None; cualquier fallo de I/O o
    decodificación durante la generación de un crop que debía existir se propaga inmediatamente.
    """
    review_dir = review_dir.resolve()
    canonical_base_dir = canonical_base_dir.resolve()

    # Validar confinamiento de review_dir bajo canonical_base_dir
    validate_safe_crop_path(review_dir, canonical_base_dir)

    review_dir.mkdir(parents=True, exist_ok=True)

    result_issues: list[ReviewIssue] = []
    seen_filenames: set[str] = set()

    for issue in issues:
        # Copia no mutativa
        issue_copy = issue.model_copy(deep=True)

        if issue.page is None:
            issue_copy.crop_path = None
            result_issues.append(issue_copy)
            continue

        bbox = match_region_for_issue(issue, regions_map)
        page_img = page_images.get(issue.page)

        if bbox is None or page_img is None or not page_img.is_file():
            # Sin región válida o sin imagen contractual -> crop_path nulo
            issue_copy.crop_path = None
            result_issues.append(issue_copy)
            continue

        # Generar nombre determinista sin colisiones
        suffix_idx = 0
        crop_filename = generate_crop_filename(
            page=issue.page,
            field=issue.field,
            row_key=issue.row_key,
            warning_code=issue.warning_code,
            index=suffix_idx,
        )
        while crop_filename in seen_filenames:
            suffix_idx += 1
            crop_filename = generate_crop_filename(
                page=issue.page,
                field=issue.field,
                row_key=issue.row_key,
                warning_code=issue.warning_code,
                index=suffix_idx,
            )
        seen_filenames.add(crop_filename)

        crop_target = review_dir / crop_filename

        # Ejecutar recorte sin enmascarar fallos de disco o decodificación
        crop_image_region(
            image_path=page_img,
            bbox=bbox,
            output_path=crop_target,
            allowed_dir=review_dir,
        )

        issue_copy.crop_path = f"review/{crop_filename}"
        result_issues.append(issue_copy)

    return result_issues