import os

def extract_pdf_images(pdf_path: str, output_dir: str) -> list[str]:
    """Convierte páginas del PDF a imágenes."""
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    pdf_img_dir = os.path.join(output_dir, "pdf_pages")
    os.makedirs(pdf_img_dir, exist_ok=True)
    image_paths = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=300)
        img_path = os.path.join(pdf_img_dir, f"page_{i+1}.png")
        pix.save(img_path)
        image_paths.append(img_path)
    doc.close()
    return image_paths
