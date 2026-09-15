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
            try:
                free_bytes, total_bytes = torch.cuda.mem_get_info()
                free_mb = free_bytes / (1024 * 1024)
            except Exception:
                free_mb = 4000

            if free_mb < 2500:
                print(f"Aviso: Memoria VRAM libre baja ({free_mb:.0f} MiB). Liberando caché CUDA...")
                torch.cuda.empty_cache()
                try:
                    free_bytes, _ = torch.cuda.mem_get_info()
                    free_mb = free_bytes / (1024 * 1024)
                except Exception:
                    pass

            if free_mb >= 2000:
                self.device = "cuda"
                self.dtype = torch.bfloat16
                gpu_name = torch.cuda.get_device_name(0)
                print(f"GPU activada: {gpu_name} (aceleración CUDA activada, {free_mb:.0f} MiB VRAM libre)")
            else:
                self.device = "cpu"
                self.dtype = torch.float32
                print(
                    f"VRAM libre insuficiente ({free_mb:.0f} MiB < 2000 MiB requeridos). "
                    "Ejecutando Unlimited-OCR en modo CPU para prevenir caídas de memoria..."
                )
        else:
            self.device = "cpu"
            self.dtype = torch.float32
            print("GPU no detectada en PyTorch. Ejecutando en modo CPU...")

        try:
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
        except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
            if self.device == "cuda":
                print(f"Aviso: Fallo al asignar modelo en GPU ({exc}). Reintentando en modo CPU...")
                self.device = "cpu"
                self.dtype = torch.float32
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
            else:
                raise

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
                    no_repeat_ngram_size=15,
                    ngram_window=0,
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
                    no_repeat_ngram_size=15,
                    ngram_window=0,
                    save_results=True,
                )

        result_file = os.path.join(self.output_dir, "result.md")
        raw_text = ""
        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8", newline="") as f:
                raw_text = f.read()

        raw_dir = os.path.join(self.output_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        _clean_existing_page_files(raw_dir)
        _persist_raw_document(raw_dir, raw_text, self.output_dir, result_file)
        with open(os.path.join(raw_dir, "page_001.md"), "w", encoding="utf-8", newline="") as f:
            f.write(raw_text)

        return raw_text

    def extract_from_images(self, image_paths: list[str]) -> str:
        """Extrae el contenido de múltiples imágenes (ej. páginas de PDF) procesando cada página secuencialmente de forma optimizada."""
        import torch

        if not image_paths:
            return ""

        print(f"Procesando {len(image_paths)} páginas secuencialmente con Unlimited-OCR...")
        page_texts = []
        global_images_dir = os.path.join(self.output_dir, "images")
        os.makedirs(global_images_dir, exist_ok=True)

        for idx, image_path in enumerate(image_paths, start=1):
            print(f"[{idx}/{len(image_paths)}] Procesando página: {image_path}")
            page_work_dir = os.path.join(self.output_dir, f"_page_work_{idx}")
            os.makedirs(page_work_dir, exist_ok=True)

            try:
                with torch.inference_mode():
                    self.model.infer(
                        self.tokenizer,
                        prompt="<image>document parsing.",
                        image_file=image_path,
                        output_path=page_work_dir,
                        base_size=1024,
                        image_size=640,
                        crop_mode=True,
                        max_length=32768,
                        no_repeat_ngram_size=15,
                        ngram_window=0,
                        save_results=True,
                    )
            except torch.cuda.OutOfMemoryError:
                print(f"Memoria VRAM agotada en GPU para página {idx}. Reintentando con configuración ligera...")
                with torch.inference_mode():
                    self.model.infer(
                        self.tokenizer,
                        prompt="<image>document parsing.",
                        image_file=image_path,
                        output_path=page_work_dir,
                        base_size=512,
                        image_size=384,
                        crop_mode=False,
                        max_length=32768,
                        no_repeat_ngram_size=15,
                        ngram_window=0,
                        save_results=True,
                    )

            page_result_file = os.path.join(page_work_dir, "result.md")
            p_text = ""
            if os.path.exists(page_result_file):
                with open(page_result_file, "r", encoding="utf-8", newline="") as f:
                    p_text = f.read()

            # Copiar y renombrar imágenes de recorte para evitar colisiones entre páginas
            page_img_dir = os.path.join(page_work_dir, "images")
            if os.path.isdir(page_img_dir):
                for img_name in os.listdir(page_img_dir):
                    src_img = os.path.join(page_img_dir, img_name)
                    if os.path.isfile(src_img):
                        dest_name = f"page_{idx}_{img_name}"
                        dest_path = os.path.join(global_images_dir, dest_name)
                        shutil.copy2(src_img, dest_path)
                        p_text = p_text.replace(f"images/{img_name}", f"images/{dest_name}")

            shutil.rmtree(page_work_dir, ignore_errors=True)
            page_texts.append(p_text)

        # Construir bloques de páginas delimitados con <PAGE>
        page_blocks = []
        for p_text in page_texts:
            p_clean = p_text
            page_blocks.append(p_clean)
        combined_text = "".join(f"<PAGE>{block}" for block in page_blocks)

        result_file = os.path.join(self.output_dir, "result.md")
        with open(result_file, "w", encoding="utf-8", newline="") as f:
            f.write(combined_text)

        return combined_text

    def process_page_artifacts(
        self,
        artifacts: list[PageArtifact],
        raw_text: str,
        source_result_path: Optional[str] = None,
    ) -> list[PageArtifact]:
        """Procesa y asigna los artefactos de página a partir del texto extraído."""
        result_src = source_result_path or os.path.join(self.output_dir, "result.md")
        return process_page_artifacts(artifacts, raw_text, self.output_dir, result_src)

    def unload(self) -> None:
        """Descarga explícitamente el modelo y tokenizer de RAM y GPU VRAM."""
        import gc

        if hasattr(self, "model") and self.model is not None:
            try:
                del self.model
            except Exception:
                pass
            self.model = None

        if hasattr(self, "tokenizer") and self.tokenizer is not None:
            try:
                del self.tokenizer
            except Exception:
                pass
            self.tokenizer = None

        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
        except Exception:
            pass
