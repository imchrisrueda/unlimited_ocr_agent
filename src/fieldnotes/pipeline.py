import os
import shutil
import tempfile
from .config import setup_encoding, get_lm_studio_url, get_lm_studio_api_key
from .ocr.unlimited import UnlimitedOCR
from .ingest.pdf import extract_pdf_images
from .vlm.lmstudio import LMStudioClient
from .export import export_to_markdown, export_to_pdf

setup_encoding()

class UnlimitedOCRAgent:
    def __init__(self, model_name="baidu/Unlimited-OCR", output_dir="./output_ocr", lm_model=None):
        self.model_name = model_name
        self.base_output_dir = output_dir
        os.makedirs(self.base_output_dir, exist_ok=True)
        self.output_dir = tempfile.mkdtemp(prefix="run_", dir=self.base_output_dir)
        # Inicializar submódulos usando una variable local primero
        initial_lm_model = lm_model or os.getenv("LM_STUDIO_MODEL")
        self.ocr = UnlimitedOCR(model_name=self.model_name, output_dir=self.output_dir)
        self.vlm = LMStudioClient(
            base_url=get_lm_studio_url(),
            api_key=get_lm_studio_api_key(),
            default_model=initial_lm_model
        )

    @property
    def tokenizer(self):
        return self.ocr.tokenizer

    @tokenizer.setter
    def tokenizer(self, value):
        self.ocr.tokenizer = value

    @property
    def device(self):
        return self.ocr.device

    @device.setter
    def device(self, value):
        self.ocr.device = value

    @property
    def dtype(self):
        return self.ocr.dtype

    @dtype.setter
    def dtype(self, value):
        self.ocr.dtype = value

    @property
    def model(self):
        return self.ocr.model

    @model.setter
    def model(self, value):
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
        return self.ocr.extract_from_image(image_path)

    def extract_from_pdf(self, pdf_path: str) -> str:
        """Convierte páginas del PDF a imágenes y extrae su texto."""
        print(f"Procesando documento PDF: {pdf_path}")
        image_paths = extract_pdf_images(pdf_path, self.output_dir)
        return self.ocr.extract_from_images(image_paths)

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
