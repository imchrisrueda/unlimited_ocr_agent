def export_to_markdown(text: str, output_path: str) -> str:
    """Guarda el contenido de texto/markdown en un archivo .md"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Documento digitalizado guardado en Markdown: {output_path}")
    return output_path

def export_to_pdf(text: str, output_path: str) -> str:
    """Convierte el texto/markdown procesado a un archivo PDF estilizado."""
    import fitz  # PyMuPDF
    doc = fitz.open()
    page_width, page_height = 595.28, 841.89  # Tamaño A4
    margin = 40
    rect = fitz.Rect(margin, margin, page_width - margin, page_height - margin)
    
    html_text = text.replace("\n", "<br/>")
    html_content = f"""
    <div style="font-family: Helvetica, Arial, sans-serif; font-size: 11pt; line-height: 1.5; color: #222;">
        {html_text}
    </div>
    """
    
    page = doc.new_page(width=page_width, height=page_height)
    try:
        page.insert_htmlbox(rect, html_content)
    except Exception:
        page.insert_text((margin, margin), text[:4000], fontsize=10)
        
    doc.save(output_path)
    doc.close()
    print(f"Documento digitalizado guardado en PDF: {output_path}")
    return output_path
