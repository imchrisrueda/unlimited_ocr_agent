import os
from pathlib import Path
import re
import shutil
from typing import Literal, Optional, Tuple
from ..artifacts import PageArtifact

PAGE_FILE_PATTERN = re.compile(r"^page_\d{3}\.md$")


def split_page_blocks(
    raw_text: str, num_pages: int
) -> Tuple[Literal["mapped", "unaligned"], Optional[list[str]], Optional[str]]:
    """Segmenta deterministamente el texto de infer_multi por delimitadores <PAGE>.

    Reglas:
    - Retira exclusivamente el delimitador estructural <PAGE> sin modificar, truncar ni normalizar espacios o saltos de línea internos.
    - Si existe texto no vacío antes del primer <PAGE>, el mapeo se considera unaligned y no se divide.
    - Si el número de bloques no coincide exactamente con num_pages, el mapeo se considera unaligned.
    - Si num_pages es 0 o raw_text está vacío, se reporta unaligned (salvo num_pages == 0 y raw_text vacío).
    """
    if num_pages == 0:
        if not raw_text:
            return "mapped", [], None
        return "unaligned", None, "Se recibió texto de OCR pero se esperaban 0 páginas."

    if not raw_text:
        return "unaligned", None, f"El resultado de OCR está vacío pero se esperaban {num_pages} páginas."

    if "<PAGE>" not in raw_text:
        return "unaligned", None, "No se encontraron delimitadores <PAGE> en el documento OCR."

    parts = raw_text.split("<PAGE>")
    preface = parts[0]
    if preface.strip() != "":
        return (
            "unaligned",
            None,
            "Se detectó texto no vacío previo al primer delimitador <PAGE>.",
        )

    blocks = parts[1:]
    if len(blocks) != num_pages:
        return (
            "unaligned",
            None,
            f"Discrepancia en el conteo de páginas: se esperaban {num_pages} páginas pero se obtuvieron {len(blocks)} bloques <PAGE>.",
        )

    return "mapped", blocks, None


def _clean_existing_page_files(raw_dir: str) -> None:
    """Elimina con seguridad únicamente los archivos que coincidan exactamente con page_ddd.md, preservando cualquier otro archivo."""
    if not os.path.isdir(raw_dir):
        return
    for item in os.listdir(raw_dir):
        if PAGE_FILE_PATTERN.match(item):
            item_path = os.path.join(raw_dir, item)
            if os.path.isfile(item_path):
                os.remove(item_path)


def _persist_raw_document(
    raw_dir: str, raw_text: str, output_dir: str, source_result_path: Optional[str] = None
) -> None:
    """Copia result.md en binario a raw/document.md si existe dentro del run, o usa fallback con newline=''."""
    doc_path = os.path.join(raw_dir, "document.md")
    candidate_src = source_result_path or os.path.join(output_dir, "result.md")

    if candidate_src and os.path.isfile(candidate_src):
        try:
            real_src = os.path.abspath(os.path.realpath(candidate_src))
            real_out = os.path.abspath(os.path.realpath(output_dir))
            if os.path.commonpath([real_src, real_out]) == real_out:
                with open(real_src, "rb") as f_in, open(doc_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                return
        except Exception:
            pass

    with open(doc_path, "w", encoding="utf-8", newline="") as f:
        f.write(raw_text)


def process_page_artifacts(
    artifacts: list[PageArtifact],
    raw_text: str,
    output_dir: str,
    source_result_path: Optional[str] = None,
) -> list[PageArtifact]:
    """Persiste raw/document.md (byte/textualmente idéntico) y los bloques individuales si el mapeo es exitoso."""
    raw_dir = os.path.join(output_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    # Limpiar únicamente archivos de página obsoletos previos
    _clean_existing_page_files(raw_dir)

    # raw/document.md siempre se guarda con el contenido binario/textual exacto e inalterado
    _persist_raw_document(raw_dir, raw_text, output_dir, source_result_path)

    status, blocks, error_msg = split_page_blocks(raw_text, len(artifacts))

    if status == "mapped" and blocks is not None:
        for artifact, block in zip(artifacts, blocks):
            artifact.raw_ocr = block
            artifact.ocr_mapping_status = "mapped"
            artifact.mapping_error = None
            page_file = os.path.join(raw_dir, f"page_{artifact.page_number:03d}.md")
            with open(page_file, "w", encoding="utf-8", newline="") as f:
                f.write(block)
    else:
        for artifact in artifacts:
            artifact.raw_ocr = None
            artifact.ocr_mapping_status = "unaligned"
            artifact.mapping_error = error_msg

    return artifacts


class UnlimitedOCR:
    def __init__(self, model_name: str, output_dir: str):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.model_name = model_name
        self.output_dir = output_dir

        print("Cargando modelo de visión Unlimited-OCR...")
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name, trust_remote_code=True
        )

        # Detección y selección automática de hardware (GPU vs CPU)
        if torch.cuda.is_available():
            self.device = "cuda"
            self.dtype = torch.bfloat16
            gpu_name = torch.cuda.get_device_name(0)
            print(f"GPU activada: {gpu_name} (aceleración CUDA activada)")
        else:
            self.device = "cpu"
            self.dtype = torch.float32
            print("GPU no detectada en PyTorch. Ejecutando en modo CPU...")

        self.model = (
            AutoModel.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                use_safetensors=True,
                torch_dtype=self.dtype,
            )
            .eval()
            .to(self.device)
        )

    def extract_from_image(self, image_path: str) -> str:
        """Extrae el contenido de una imagen usando Unlimited-OCR."""
        import torch

        print(f"Procesando imagen: {image_path}")
        try:
            with torch.inference_mode():
                self.model.infer(
                    self.tokenizer,
                    prompt="<image>document parsing.",
                    image_file=image_path,
                    output_path=self.output_dir,
                    base_size=1024,
                    image_size=640,
                    crop_mode=True,
                    max_length=32768,
                    no_repeat_ngram_size=35,
                    ngram_window=128,
                    save_results=True,
                )
        except torch.cuda.OutOfMemoryError:
            print("Memoria VRAM agotada en GPU. Reintentando con configuración ligera...")
            with torch.inference_mode():
                self.model.infer(
                    self.tokenizer,
                    prompt="<image>document parsing.",
                    image_file=image_path,
                    output_path=self.output_dir,
                    base_size=512,
                    image_size=384,
                    crop_mode=False,
                    max_length=32768,
                    no_repeat_ngram_size=35,
                    ngram_window=128,
                    save_results=True,
                )

        result_file = os.path.join(self.output_dir, "result.md")
        raw_text = ""
        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8") as f:
                raw_text = f.read()

        raw_dir = os.path.join(self.output_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        _clean_existing_page_files(raw_dir)
        _persist_raw_document(raw_dir, raw_text, self.output_dir, result_file)
        with open(os.path.join(raw_dir, "page_001.md"), "w", encoding="utf-8", newline="") as f:
            f.write(raw_text)

        return raw_text

    def extract_from_images(self, image_paths: list[str]) -> str:
        """Extrae el contenido de múltiples imágenes (ej. páginas de PDF) usando Unlimited-OCR."""
        print(f"Procesando {len(image_paths)} páginas con infer_multi...")
        self.model.infer_multi(
            self.tokenizer,
            prompt="<image>Multi page parsing.",
            image_files=image_paths,
            output_path=self.output_dir,
            image_size=1024,
            max_length=32768,
            no_repeat_ngram_size=35,
            ngram_window=1024,
            save_results=True,
        )

        result_file = os.path.join(self.output_dir, "result.md")
        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def process_page_artifacts(
        self,
        artifacts: list[PageArtifact],
        raw_text: str,
        source_result_path: Optional[str] = None,
    ) -> list[PageArtifact]:
        """Procesa y asigna los artefactos de página a partir del texto extraído."""
        result_src = source_result_path or os.path.join(self.output_dir, "result.md")
        return process_page_artifacts(artifacts, raw_text, self.output_dir, result_src)
