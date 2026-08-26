import os
from pathlib import Path
import shutil
import tempfile
from typing import Optional, Literal
from .artifacts import PageArtifact
from .config import setup_encoding, get_lm_studio_url, get_lm_studio_api_key
from .ingest.pdf import extract_pdf_images, extract_pdf_page_artifacts
from .ocr.worker import run_ocr_worker
from .vlm.lmstudio import LMStudioClient
from .export import export_to_markdown, export_to_pdf

setup_encoding()


class UnlimitedOCRAgent:
    def __init__(
        self,
        model_name: str = "baidu/Unlimited-OCR",
        output_dir: str = "./output_ocr",
        lm_model: Optional[str] = None,
        ocr_mode: Literal["worker", "in_process"] = "worker",
        worker_timeout: Optional[int] = None,
    ):
        if ocr_mode not in ("worker", "in_process"):
            raise ValueError(f"Invalid ocr_mode: {ocr_mode}. Must be 'worker' or 'in_process'.")

        if worker_timeout is not None:
            if not isinstance(worker_timeout, int) or isinstance(worker_timeout, bool) or worker_timeout <= 0:
                raise ValueError(f"worker_timeout must be a positive integer, got: {worker_timeout!r}")

        self.model_name = model_name

        self.base_output_dir = output_dir
        self.ocr_mode = ocr_mode
        self.worker_timeout = worker_timeout
        os.makedirs(self.base_output_dir, exist_ok=True)
        self.output_dir = tempfile.mkdtemp(prefix="run_", dir=self.base_output_dir)
        self.page_artifacts: list[PageArtifact] = []

        initial_lm_model = lm_model or os.getenv("LM_STUDIO_MODEL")
        if self.ocr_mode == "in_process":
            from .ocr.unlimited import UnlimitedOCR
            self.ocr = UnlimitedOCR(model_name=self.model_name, output_dir=self.output_dir)
        else:
            self.ocr = None

        self.vlm = LMStudioClient(
            base_url=get_lm_studio_url(),
            api_key=get_lm_studio_api_key(),
            default_model=initial_lm_model,
        )

    def _check_in_process_property(self, prop_name: str) -> None:
        if self.ocr_mode != "in_process" or self.ocr is None:
            raise RuntimeError(
                f"Property '{prop_name}' is not available when ocr_mode='worker' because "
                "Unlimited-OCR is executed in an isolated subprocess to release VRAM. "
                "Instantiate UnlimitedOCRAgent with ocr_mode='in_process' if direct Python "
                "access to PyTorch/Transformers objects is required."
            )

    @property
    def tokenizer(self):
        self._check_in_process_property("tokenizer")
        return self.ocr.tokenizer

    @tokenizer.setter
    def tokenizer(self, value):
        self._check_in_process_property("tokenizer")
        self.ocr.tokenizer = value

    @property
    def device(self):
        self._check_in_process_property("device")
        return self.ocr.device

    @device.setter
    def device(self, value):
        self._check_in_process_property("device")
        self.ocr.device = value

    @property
    def dtype(self):
        self._check_in_process_property("dtype")
        return self.ocr.dtype

    @dtype.setter
    def dtype(self, value):
        self._check_in_process_property("dtype")
        self.ocr.dtype = value

    @property
    def model(self):
        self._check_in_process_property("model")
        return self.ocr.model

    @model.setter
    def model(self, value):
        self._check_in_process_property("model")
        self.ocr.model = value

    @property
    def lm_model(self):
        return self.vlm.model

    @lm_model.setter
    def lm_model(self, value):
        self.vlm.model = value

    @property
    def llm_client(self):
        return self.vlm.client

    @llm_client.setter
    def llm_client(self, value):
        self.vlm.client = value

    def cleanup(self) -> None:
        """Elimina los archivos intermedios generados durante esta ejecución."""
        if os.path.isdir(self.output_dir):
            shutil.rmtree(self.output_dir)

    def extract_from_image(self, image_path: str) -> str:
        """Extrae el contenido de una imagen usando Unlimited-OCR."""
        if self.ocr_mode == "in_process":
            raw_text = self.ocr.extract_from_image(image_path)
            self.page_artifacts = [
                PageArtifact(
                    page_number=1,
                    image_path=Path(image_path),
                    raw_ocr=raw_text,
                    ocr_mapping_status="mapped",
                    mapping_error=None,
                )
            ]
            return raw_text

        raw_text, page_meta_list, resp = run_ocr_worker(
            mode="image",
            model_name=self.model_name,
            image_paths=[image_path],
            output_dir=self.output_dir,
            timeout=self.worker_timeout,
        )
        self.page_artifacts = [
            PageArtifact(
                page_number=1,
                image_path=Path(image_path),
                raw_ocr=raw_text,
                ocr_mapping_status="mapped",
                mapping_error=None,
            )
        ]
        return raw_text

    def extract_from_pdf(self, pdf_path: str) -> str:
        """Convierte páginas del PDF a imágenes y extrae su texto."""
        print(f"Procesando documento PDF: {pdf_path}")
        artifacts = extract_pdf_page_artifacts(pdf_path, self.output_dir)
        image_paths = [str(a.image_path) for a in artifacts]

        if self.ocr_mode == "in_process":
            raw_text = self.ocr.extract_from_images(image_paths)
            self.page_artifacts = self.ocr.process_page_artifacts(artifacts, raw_text)
            return raw_text

        raw_text, page_meta_list, resp = run_ocr_worker(
            mode="pdf",
            model_name=self.model_name,
            image_paths=image_paths,
            output_dir=self.output_dir,
            timeout=self.worker_timeout,
        )

        mapping_status = resp.get("mapping_status", "unaligned")
        mapping_error = resp.get("mapping_error")

        raw_dir = os.path.join(self.output_dir, "raw")
        for a in artifacts:
            if mapping_status == "mapped":
                page_file = os.path.join(raw_dir, f"page_{a.page_number:03d}.md")
                if os.path.isfile(page_file):
                    with open(page_file, "r", encoding="utf-8") as f:
                        a.raw_ocr = f.read()
                else:
                    a.raw_ocr = None
                a.ocr_mapping_status = "mapped"
                a.mapping_error = None
            else:
                a.raw_ocr = None
                a.ocr_mapping_status = "unaligned"
                a.mapping_error = mapping_error

        self.page_artifacts = artifacts
        return raw_text

    def extract_page_artifacts_from_pdf(self, pdf_path: str) -> list[PageArtifact]:
        """Procesa un PDF y retorna la lista de PageArtifacts con su estado y OCR."""
        self.extract_from_pdf(pdf_path)
        return self.page_artifacts

    def _resolve_lm_model(self) -> str:
        """Obtiene el identificador de un modelo de texto disponible en LM Studio."""
        self.lm_model = self.vlm.resolve_model()
        return self.lm_model

    def ask_lmstudio(
        self,
        document_text: str,
        question: str,
        reasoning_effort: str = "none",
        max_tokens: int = 2048,
    ) -> str:
        """Envía el contenido del documento extraído a LM Studio para análisis."""
        return self.vlm.ask(document_text, question, reasoning_effort, max_tokens)

    def ask_lmstudio_chunked(
        self,
        document_text: str,
        question: str,
        chunk_size: int,
        chunk_overlap: int,
        reasoning_effort: str,
        max_tokens: int,
    ) -> str:
        """Resume documentos largos en fragmentos y sintetiza el resultado."""
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap debe ser menor que chunk_size")

        chunks = []
        start = 0
        while start < len(document_text):
            end = min(start + chunk_size, len(document_text))
            chunks.append(document_text[start:end])
            if end == len(document_text):
                break
            start = end - chunk_overlap

        partials = []
        for index, chunk in enumerate(chunks, start=1):
            print(f"Analizando fragmento {index}/{len(chunks)}...")
            partial = self.ask_lmstudio(
                chunk,
                "Analiza únicamente este fragmento y extrae los datos relevantes para la tarea. "
                + question,
                reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
            )
            if partial.strip():
                partials.append(f"### Fragmento {index}\n{partial}")

        if not partials:
            return ""

        print("Sintetizando los resultados parciales...")
        return self.ask_lmstudio(
            "\n\n".join(partials),
            "Combina los análisis parciales en una respuesta única, coherente y fiel al documento. "
            + question,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )

    def export_to_markdown(self, text: str, output_path: str) -> str:
        """Guarda el contenido de texto/markdown en un archivo .md"""
        return export_to_markdown(text, output_path)

    def export_to_pdf(self, text: str, output_path: str) -> str:
        """Convierte el texto/markdown procesado a un archivo PDF estilizado."""
        return export_to_pdf(text, output_path)
