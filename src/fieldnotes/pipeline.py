import os
from pathlib import Path
import shutil
import tempfile
from typing import Optional, Literal, Any, TypeVar, Type
from pydantic import BaseModel
from .artifacts import PageArtifact
from .config import setup_encoding, get_lm_studio_url, get_lm_studio_api_key, get_lm_studio_timeout
from .ingest.pdf import extract_pdf_images, extract_pdf_page_artifacts
from .ocr.worker import run_ocr_worker
from .vlm.lmstudio import LMStudioClient
from .export import export_to_markdown, export_to_pdf

T = TypeVar("T", bound=BaseModel)

setup_encoding()


class UnlimitedOCRAgent:
    def __init__(
        self,
        model_name: str = "baidu/Unlimited-OCR",
        output_dir: str = "./output_ocr",
        vision_model: Optional[str] = None,
        text_model: Optional[str] = None,
        lm_model: Optional[str] = None,
        ocr_mode: Literal["worker", "in_process"] = "worker",
        worker_timeout: Optional[int] = None,
        validate_model: bool = True,
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

        if self.ocr_mode == "in_process":
            from .ocr.unlimited import UnlimitedOCR
            self.ocr = UnlimitedOCR(model_name=self.model_name, output_dir=self.output_dir)
        else:
            self.ocr = None

        self.vlm = LMStudioClient(
            base_url=get_lm_studio_url(),
            api_key=get_lm_studio_api_key(),
            vision_model=vision_model,
            text_model=text_model,
            timeout=get_lm_studio_timeout(),
            default_model=lm_model,
            validate_model=validate_model,
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
    def lm_model(self) -> Optional[str]:
        return self.vlm.model

    @lm_model.setter
    def lm_model(self, value: Optional[str]) -> None:
        self.vlm.model = value

    @property
    def vision_model(self) -> Optional[str]:
        return self.vlm.vision_model

    @vision_model.setter
    def vision_model(self, value: Optional[str]) -> None:
        self.vlm.vision_model = value

    @property
    def text_model(self) -> Optional[str]:
        return self.vlm.text_model

    @text_model.setter
    def text_model(self, value: Optional[str]) -> None:
        self.vlm.text_model = value

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
        self.lm_model = self.vlm.resolve_model(model_type="text")
        return self.lm_model

    def ask_lmstudio(
        self,
        document_text: str,
        question: str,
        reasoning_effort: str = "none",
        max_tokens: int = 2048,
    ) -> str:
        """Envía el contenido del documento extraído a LM Studio para análisis de texto."""
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
        return self.vlm.ask_chunked(
            document_text=document_text,
            question=question,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )

    def ask_vision(
        self,
        image_path: str | Path,
        prompt: str,
        ocr_context: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Envía una imagen (fuente primaria) y contexto OCR a LM Studio (secuencial tras OCR)."""
        return self.vlm.ask_vision(
            image_path=image_path,
            prompt=prompt,
            ocr_context=ocr_context,
            **kwargs,
        )

    def ask_vision_structured(
        self,
        image_path: str | Path,
        prompt: str,
        schema: Type[T],
        ocr_context: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        """Envía una consulta visual estructurada a LM Studio retornando una instancia tipada."""
        return self.vlm.ask_vision_structured(
            image_path=image_path,
            prompt=prompt,
            schema=schema,
            ocr_context=ocr_context,
            **kwargs,
        )

    def ask_page_vision(
        self,
        page_artifact: PageArtifact,
        prompt: str,
        **kwargs: Any,
    ) -> str:
        """Envía la imagen de un PageArtifact y su OCR de apoyo correspondiente a LM Studio."""
        return self.vlm.ask_vision(
            image_path=page_artifact.image_path,
            prompt=prompt,
            ocr_context=page_artifact.raw_ocr,
            **kwargs,
        )

    def ask_page_vision_structured(
        self,
        page_artifact: PageArtifact,
        prompt: str,
        schema: Type[T],
        **kwargs: Any,
    ) -> T:
        """Envía la imagen y OCR de apoyo de un PageArtifact retornando una instancia tipada."""
        return self.vlm.ask_vision_structured(
            image_path=page_artifact.image_path,
            prompt=prompt,
            schema=schema,
            ocr_context=page_artifact.raw_ocr,
            **kwargs,
        )

    def export_to_markdown(self, text: str, output_path: str) -> str:
        """Guarda el contenido de texto/markdown en un archivo .md"""
        return export_to_markdown(text, output_path)

    def export_to_pdf(self, text: str, output_path: str) -> str:
        """Convierte el texto/markdown procesado a un archivo PDF estilizado."""
        return export_to_pdf(text, output_path)

    def process_estadillo(
        self,
        file_path: str | Path,
        output_dir: Optional[str | Path] = None,
        prompt: Optional[str] = None,
        vision_model: Optional[str] = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ):
        """Procesa un archivo PDF o imagen bajo el perfil de dominio 'estadillo'.

        Retorna una tupla (markdown_text, estadillo_doc) y persiste el layout
        canónico en <output_dir>/<stem>/.
        """
        from .profiles.estadillo import EstadilloProfile
        profile = EstadilloProfile(
            agent=self,
            output_base_dir=output_dir,
            vision_model=vision_model,
        )
        return profile.run(file_path=file_path, prompt=prompt, max_tokens=max_tokens, **kwargs)

    def process_notebook(
        self,
        file_path: str | Path,
        output_dir: Optional[str | Path] = None,
        vision_model: Optional[str] = None,
        config: Optional[Any] = None,
        prompt: Optional[str] = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ):
        """Procesa un archivo PDF o imagen bajo el perfil general 'notebook'.

        Retorna una tupla (markdown_text, notebook_doc) y persiste el layout
        canónico en <output_dir>/<stem>/.
        """
        from .profiles.notebook import NotebookProfile
        profile = NotebookProfile(
            agent=self,
            output_base_dir=output_dir,
            vision_model=vision_model,
            config=config,
        )
        return profile.run(file_path=file_path, prompt=prompt, max_tokens=max_tokens, **kwargs)

    def process_cuaderno_campo(self, file_path: str | Path, output_dir: Optional[str | Path] = None, vision_model: Optional[str] = None, config: Optional[Any] = None, prompt: Optional[str] = None, max_tokens: int = 4096, **kwargs: Any):
        """Procesa un cuaderno visual secuencial sin tablas de estadillo."""
        from .profiles.notebook import CuadernoCampoProfile
        profile = CuadernoCampoProfile(agent=self, output_base_dir=output_dir, vision_model=vision_model, config=config)
        return profile.run(file_path=file_path, prompt=prompt, max_tokens=max_tokens, **kwargs)

    def process_diagram(
        self,
        image_path: str | Path,
        diagram_type: str,
        output_dir: Optional[str | Path] = None,
        page_number: int = 1,
        ocr_context: Optional[str] = None,
        prompt: Optional[str] = None,
        max_tokens: int = 4096,
        render: bool = False,
        document_name: Optional[str] = None,
        **kwargs: Any,
    ):
        """Extrae un DiagramIR estructurado desde una imagen y opcionalmente persiste los artefactos.

        Si render=True, genera y publica además los artefactos visuales derivados (SVG/Mermaid)
        y la descripción Markdown accesible.
        Retorna una tupla (diagram_ir, persisted_artifacts_dict_or_None).
        """
        from .diagrams.extraction import process_diagram
        return process_diagram(
            image_path=image_path,
            client=self.vlm,
            diagram_type=diagram_type,  # type: ignore[arg-type]
            output_base_dir=output_dir,
            page_number=page_number,
            ocr_context=ocr_context,
            prompt=prompt,
            max_tokens=max_tokens,
            render=render,
            document_name=document_name,
            **kwargs,
        )

    def render_diagram(
        self,
        diagram: Any,
        asset_relative_path: Optional[str] = None,
    ):
        """Renderiza de forma pura y determinista un DiagramIR a sus artefactos visuales y Markdown."""
        from .diagrams.rendering import render_diagram
        return render_diagram(diagram=diagram, asset_relative_path=asset_relative_path)

    def publish_rendered_diagram(
        self,
        diagram: Any,
        source_image_path: str | Path,
        output_dir: str | Path,
        document_name: Optional[str] = None,
    ):
        """Publica de forma atómica y confinada los artefactos canónicos y derivados de DiagramIR."""
        from .diagrams.rendering import publish_rendered_diagram
        return publish_rendered_diagram(
            diagram=diagram,
            source_image_path=source_image_path,
            output_base_dir=output_dir,
            document_name=document_name,
        )
