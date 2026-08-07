import os
import sys
import argparse
import shutil
import tempfile
import fitz  # PyMuPDF
import torch
from openai import OpenAI
from transformers import AutoModel, AutoTokenizer

# Configurar codificación UTF-8 para consola de Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Configuración del servidor de LM Studio (API OpenAI local)
_lm_studio_url = os.getenv("LM_STUDIO_URL", "http://localhost:1234").rstrip("/")
LM_STUDIO_BASE_URL = _lm_studio_url if _lm_studio_url.endswith("/v1") else f"{_lm_studio_url}/v1"
LM_STUDIO_API_KEY = os.getenv("LM_STUDIO_API_KEY", "lm-studio")

class UnlimitedOCRAgent:
    def __init__(self, model_name="baidu/Unlimited-OCR", output_dir="./output_ocr", lm_model=None):
        self.model_name = model_name
        self.lm_model = lm_model or os.getenv("LM_STUDIO_MODEL")
        self.base_output_dir = output_dir
        os.makedirs(self.base_output_dir, exist_ok=True)
        self.output_dir = tempfile.mkdtemp(prefix="run_", dir=self.base_output_dir)
        
        print("Cargando modelo de visión Unlimited-OCR...")
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name, 
            trust_remote_code=True
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
        
        self.model = AutoModel.from_pretrained(
            self.model_name, 
            trust_remote_code=True, 
            use_safetensors=True, 
            torch_dtype=self.dtype
        ).eval().to(self.device)
        
        # Cliente OpenAI para conectar con el servidor local de LM Studio
        self.llm_client = OpenAI(
            base_url=LM_STUDIO_BASE_URL,
            api_key=LM_STUDIO_API_KEY
        )

    def cleanup(self) -> None:
        """Elimina los archivos intermedios generados durante esta ejecución."""
        if os.path.isdir(self.output_dir):
            shutil.rmtree(self.output_dir)

    def extract_from_image(self, image_path: str) -> str:
        """Extrae el contenido de una imagen usando Unlimited-OCR."""
        print(f"Procesando imagen: {image_path}")
        try:
            with torch.inference_mode():
                self.model.infer(
                    self.tokenizer,
                    prompt='<image>document parsing.',
                    image_file=image_path,
                    output_path=self.output_dir,
                    base_size=1024,
                    image_size=640,
                    crop_mode=True,
                    max_length=32768,
                    no_repeat_ngram_size=35,
                    ngram_window=128,
                    save_results=True
                )
        except torch.cuda.OutOfMemoryError:
            print("Memoria VRAM agotada en GPU. Reintentando con configuración ligera...")
            with torch.inference_mode():
                self.model.infer(
                    self.tokenizer,
                    prompt='<image>document parsing.',
                    image_file=image_path,
                    output_path=self.output_dir,
                    base_size=512,
                    image_size=384,
                    crop_mode=False,
                    max_length=32768,
                    no_repeat_ngram_size=35,
                    ngram_window=128,
                    save_results=True
                )

        result_file = os.path.join(self.output_dir, "result.md")
        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def extract_from_pdf(self, pdf_path: str) -> str:
        """Convierte páginas del PDF a imágenes y extrae su texto."""
        print(f"Procesando documento PDF: {pdf_path}")
        doc = fitz.open(pdf_path)
        pdf_img_dir = os.path.join(self.output_dir, "pdf_pages")
        os.makedirs(pdf_img_dir, exist_ok=True)
        image_paths = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=300)
            img_path = os.path.join(pdf_img_dir, f"page_{i+1}.png")
            pix.save(img_path)
            image_paths.append(img_path)
        doc.close()

        print(f"Procesando {len(image_paths)} páginas con infer_multi...")
        self.model.infer_multi(
            self.tokenizer,
            prompt='<image>Multi page parsing.',
            image_files=image_paths,
            output_path=self.output_dir,
            image_size=1024,
            max_length=32768,
            no_repeat_ngram_size=35,
            ngram_window=1024,
            save_results=True
        )

        result_file = os.path.join(self.output_dir, "result.md")
        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def _resolve_lm_model(self) -> str:
        """Obtiene el identificador de un modelo de texto disponible en LM Studio."""
        if self.lm_model:
            return self.lm_model

        models = self.llm_client.models.list().data
        candidates = [
            model.id for model in models
            if "embedding" not in model.id.lower()
        ]
        if not candidates:
            raise RuntimeError(
                "LM Studio no anuncia ningún modelo de texto en /v1/models. "
                "Carga un modelo compatible y vuelve a intentarlo."
            )
        self.lm_model = candidates[0]
        print(f"Modelo seleccionado en LM Studio: {self.lm_model}")
        return self.lm_model

    def ask_lmstudio(self, document_text: str, question: str) -> str:
        """Envía el contenido del documento extraído a LM Studio para análisis."""
        print("Consultando al modelo en LM Studio...")
        system_prompt = (
            "Eres un agente IA especializado en analizar y digitalizar documentos procesados por OCR. "
            "Responde de forma precisa, limpia y bien estructurada en formato Markdown."
        )
        user_prompt = (
            f"=== DOCUMENTO EXTRAÍDO POR UNLIMITED-OCR ===\n"
            f"{document_text}\n"
            f"=============================================\n\n"
            f"INSTRUCCIÓN DEL USUARIO: {question}"
        )
        
        try:
            response = self.llm_client.chat.completions.create(
                model=self._resolve_lm_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                reasoning_effort="none"
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                print(
                    "Ayuda: LM Studio devolvió content vacío. El modelo puede haber consumido "
                    "la salida en razonamiento. Se ha solicitado reasoning_effort='none'; "
                    "si persiste, usa un modelo no razonador o aumenta max_tokens."
                )
                return ""
            return content
        except Exception as e:
            return f"Error al conectar con LM Studio: {e}\nAsegúrate de haber activado el 'Local Server' en LM Studio."

    def export_to_markdown(self, text: str, output_path: str) -> str:
        """Guarda el contenido de texto/markdown en un archivo .md"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Documento digitalizado guardado en Markdown: {output_path}")
        return output_path

    def export_to_pdf(self, text: str, output_path: str) -> str:
        """Convierte el texto/markdown procesado a un archivo PDF estilizado."""
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

def main():
    parser = argparse.ArgumentParser(description="Agente IA: Unlimited-OCR + LM Studio")
    parser.add_argument("file_path", help="Ruta de la imagen o archivo PDF a digitalizar")
    parser.add_argument("--lm-model", help="Identificador del modelo de texto de LM Studio; si se omite se detecta automáticamente")
    parser.add_argument("--instruction-file", help="Archivo Markdown o texto con la instrucción para LM Studio")
    parser.add_argument("prompt", nargs="?", default="Digitaliza este documento manteniendo su estructura en Markdown limpio.", help="Instrucción o pregunta para el agente")
    parser.add_argument("--export-md", help="Ruta donde guardar el resultado en formato Markdown (.md)")
    parser.add_argument("--export-pdf", help="Ruta donde guardar el resultado en formato PDF (.pdf)")
    parser.add_argument("--keep-intermediate", action="store_true", help="Conservar los artefactos intermedios en output_ocr")
    parser.add_argument("--raw", action="store_true", help="Obtener solo la digitalización directa de Unlimited-OCR sin consultar a LM Studio")

    args = parser.parse_args()

    instruction = args.prompt or "Digitaliza este documento manteniendo su estructura en Markdown limpio."
    if args.instruction_file:
        with open(args.instruction_file, "r", encoding="utf-8") as f:
            instruction = f.read().strip()

    agent = UnlimitedOCRAgent(lm_model=args.lm_model)
    if not args.keep_intermediate:
        import atexit
        atexit.register(agent.cleanup)

    if args.file_path.lower().endswith(".pdf"):
        raw_ocr_text = agent.extract_from_pdf(args.file_path)
    else:
        raw_ocr_text = agent.extract_from_image(args.file_path)

    print("\n--- TEXTO EXTRAÍDO POR UNLIMITED-OCR (Vista previa) ---")
    print(raw_ocr_text[:500] + ("..." if len(raw_ocr_text) > 500 else ""))
    print("-------------------------------------------------------\n")

    if args.raw:
        final_result = raw_ocr_text
    else:
        final_result = agent.ask_lmstudio(raw_ocr_text, instruction)

    print("\nRESPUESTA DEL AGENTE:")
    print(final_result)

    # Exportación si se solicitaron flags
    if args.export_md:
        agent.export_to_markdown(final_result, args.export_md)
    if args.export_pdf:
        agent.export_to_pdf(final_result, args.export_pdf)
    if args.keep_intermediate:
        print(f"Archivos intermedios conservados en: {agent.output_dir}")

if __name__ == "__main__":
    main()
