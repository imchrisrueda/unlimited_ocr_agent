import os

class UnlimitedOCR:
    def __init__(self, model_name: str, output_dir: str):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.model_name = model_name
        self.output_dir = output_dir

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

    def extract_from_image(self, image_path: str) -> str:
        """Extrae el contenido de una imagen usando Unlimited-OCR."""
        import torch
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

    def extract_from_images(self, image_paths: list[str]) -> str:
        """Extrae el contenido de múltiples imágenes (ej. páginas de PDF) usando Unlimited-OCR."""
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
