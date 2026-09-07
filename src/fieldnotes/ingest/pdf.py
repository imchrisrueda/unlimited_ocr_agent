import os
from pathlib import Path
from ..artifacts import PageArtifact


def extract_pdf_page_artifacts(pdf_path: str, output_dir: str) -> list[PageArtifact]:
    """Convierte páginas del PDF a imágenes estructuradas y retorna lista de PageArtifact."""
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    pages_dir = os.path.join(output_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)
    artifacts: list[PageArtifact] = []
    for i, page in enumerate(doc):
        page_number = i + 1
        img_path = Path(pages_dir) / f"page_{page_number:03d}.png"
        pix = page.get_pixmap(dpi=300)
        pix.save(str(img_path))
        artifacts.append(
            PageArtifact(
                page_number=page_number,
                image_path=img_path,
                raw_ocr=None,
                ocr_mapping_status="pending",
                mapping_error=None,
            )
        )
    doc.close()
    return artifacts


def extract_pdf_images(pdf_path: str, output_dir: str) -> list[str]:
    """Wrapper retrocompatible que convierte páginas del PDF a imágenes y retorna rutas como list[str]."""
    artifacts = extract_pdf_page_artifacts(pdf_path, output_dir)
    return [str(artifact.image_path) for artifact in artifacts]
