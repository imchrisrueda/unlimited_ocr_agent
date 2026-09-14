import os
import re
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Union, Optional, Sequence
from ..profiles.estadillo import safe_document_stem

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


def is_image_file(path: Union[str, Path]) -> bool:
    """Comprueba si una ruta corresponde a un formato de imagen soportado."""
    p = Path(path)
    return p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS


def natural_sort_key(path: Path) -> list:
    """Clave de ordenación natural alfanumérica para ordenar nombres como page_1, page_2, page_10."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", path.name)]


def get_image_files_from_directory(
    dir_path: Union[str, Path],
    recursive: bool = False,
) -> list[Path]:
    """Obtiene y ordena naturalmente las imágenes presentes en un directorio."""
    directory = Path(dir_path).resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"La ruta especificada no es un directorio: {directory}")

    pattern = "**/*" if recursive else "*"
    images: list[Path] = []
    for item in directory.glob(pattern):
        if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS:
            if not item.name.startswith((".", "~$")):
                images.append(item)

    images.sort(key=natural_sort_key)
    return images


def convert_images_to_pdf(
    image_paths: Sequence[Union[str, Path]],
    output_pdf_path: Union[str, Path],
) -> Path:
    """Convierte una secuencia ordenada de imágenes en un documento PDF de alta fidelidad."""
    if not image_paths:
        raise ValueError("Se requiere al menos una imagen para generar el PDF.")

    target = Path(output_pdf_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        import pymupdf  # PyMuPDF
        with pymupdf.open() as doc:
            for img_path in image_paths:
                img_file = Path(img_path).resolve()
                if not img_file.is_file():
                    raise FileNotFoundError(f"Archivo de imagen no encontrado: {img_file}")
                with pymupdf.open(str(img_file)) as img_doc:
                    pdf_bytes = img_doc.convert_to_pdf()
                with pymupdf.open("pdf", pdf_bytes) as img_pdf:
                    doc.insert_pdf(img_pdf)
            doc.save(str(target))
    except Exception as exc:
        # Fallback mediante Pillow si PyMuPDF encuentra alguna anomalía
        try:
            from PIL import Image
            with ExitStack() as resources:
                pil_images = []
                for img_path in image_paths:
                    with Image.open(Path(img_path).resolve()) as original:
                        pil_img = original.convert("RGB")
                    resources.callback(pil_img.close)
                    pil_images.append(pil_img)
                pil_images[0].save(str(target), save_all=True, append_images=pil_images[1:])
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Error al convertir imágenes a PDF '{target}': {fallback_exc}"
            ) from exc

    return target


def prepare_input_source(
    input_path: Union[str, Path],
    working_dir: Optional[Union[str, Path]] = None,
    force_pdf_conversion: bool = False,
) -> tuple[Path, bool, str]:
    """Detecta el tipo de entrada (PDF, directorio o imagen) y lo normaliza a PDF si es necesario.

    Retorna:
        tuple[Path, bool, str]: (ruta_documento_a_procesar, fue_convertido, stem_original)
    """
    source = Path(input_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"La ruta de entrada especificada no existe: {source}")

    stem = safe_document_stem(source)
    work_dir = Path(working_dir) if working_dir else Path(tempfile.mkdtemp(prefix="fieldnotes_input_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Caso Directorio con imágenes
    if source.is_dir():
        images = get_image_files_from_directory(source)
        if not images:
            supported = ", ".join(sorted(IMAGE_EXTENSIONS))
            raise ValueError(
                f"No se encontraron imágenes válidas ({supported}) en el directorio '{source}'"
            )
        target_pdf = work_dir / f"{stem}.pdf"
        convert_images_to_pdf(images, target_pdf)
        return target_pdf, True, stem

    # 2. Caso Archivo PDF
    if source.suffix.lower() == ".pdf":
        return source, False, stem

    # 3. Caso Archivo de Imagen
    if source.suffix.lower() in IMAGE_EXTENSIONS:
        if force_pdf_conversion:
            target_pdf = work_dir / f"{stem}.pdf"
            convert_images_to_pdf([source], target_pdf)
            return target_pdf, True, stem
        return source, False, stem

    raise ValueError(
        f"Tipo de archivo no soportado: '{source.name}'. Debe ser un archivo PDF, una imagen "
        f"o un directorio que contenga imágenes."
    )
