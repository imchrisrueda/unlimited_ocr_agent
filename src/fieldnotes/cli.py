import argparse
from typing import Optional
from .pipeline import UnlimitedOCRAgent


def build_parser():
    parser = argparse.ArgumentParser(description="Agente IA: Unlimited-OCR + LM Studio")
    parser.add_argument("file_path", help="Ruta de la imagen o archivo PDF a digitalizar")
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default="none",
        help="Nivel de razonamiento solicitado a LM Studio",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="Máximo de tokens generados por llamada a LM Studio",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=0,
        help="Tamaño en caracteres para resumir documentos largos por fragmentos; 0 desactiva el modo fragmentado",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=500,
        help="Solapamiento entre fragmentos en caracteres",
    )
    parser.add_argument(
        "--lm-model",
        help="Identificador del modelo de texto de LM Studio; si se omite se detecta automáticamente",
    )
    parser.add_argument(
        "--instruction-file",
        help="Archivo Markdown o texto con la instrucción para LM Studio",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Digitaliza este documento manteniendo su estructura en Markdown limpio.",
        help="Instrucción o pregunta para el agente",
    )
    parser.add_argument(
        "--export-md", help="Ruta donde guardar el resultado en formato Markdown (.md)"
    )
    parser.add_argument(
        "--export-pdf", help="Ruta donde guardar el resultado en formato PDF (.pdf)"
    )
    parser.add_argument(
        "--keep-intermediate",
        action="store_true",
        help="Conservar los artefactos intermedios en output_ocr",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Obtener solo la digitalización directa de Unlimited-OCR sin consultar a LM Studio",
    )
    parser.add_argument(
        "--ocr-mode",
        choices=("worker", "in_process"),
        default="worker",
        help="Modo de ejecución de Unlimited-OCR: 'worker' (subproceso aislado para liberar VRAM) o 'in_process' (mismo proceso)",
    )
    parser.add_argument(
        "--worker-timeout",
        type=int,
        default=None,
        help="Tiempo límite en segundos para el worker de OCR (por defecto 1800 s)",
    )
    return parser


def main(args=None):
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    if parsed_args.worker_timeout is not None and parsed_args.worker_timeout <= 0:
        parser.error("--worker-timeout must be a positive integer")

    instruction = (
        parsed_args.prompt
        or "Digitaliza este documento manteniendo su estructura en Markdown limpio."
    )

    if parsed_args.instruction_file:
        with open(parsed_args.instruction_file, "r", encoding="utf-8") as f:
            instruction = f.read().strip()

    agent = UnlimitedOCRAgent(
        lm_model=parsed_args.lm_model,
        ocr_mode=parsed_args.ocr_mode,
        worker_timeout=parsed_args.worker_timeout,
    )
    if not parsed_args.keep_intermediate:
        import atexit

        atexit.register(agent.cleanup)

    if parsed_args.file_path.lower().endswith(".pdf"):
        raw_ocr_text = agent.extract_from_pdf(parsed_args.file_path)
    else:
        raw_ocr_text = agent.extract_from_image(parsed_args.file_path)

    print("\n--- TEXTO EXTRAÍDO POR UNLIMITED-OCR (Vista previa) ---")
    print(raw_ocr_text[:500] + ("..." if len(raw_ocr_text) > 500 else ""))
    print("-------------------------------------------------------\n")

    if parsed_args.raw:
        final_result = raw_ocr_text
    elif parsed_args.chunk_size > 0 and len(raw_ocr_text) > parsed_args.chunk_size:
        final_result = agent.ask_lmstudio_chunked(
            raw_ocr_text,
            instruction,
            chunk_size=parsed_args.chunk_size,
            chunk_overlap=parsed_args.chunk_overlap,
            reasoning_effort=parsed_args.reasoning_effort,
            max_tokens=parsed_args.max_tokens,
        )
    else:
        final_result = agent.ask_lmstudio(
            raw_ocr_text,
            instruction,
            reasoning_effort=parsed_args.reasoning_effort,
            max_tokens=parsed_args.max_tokens,
        )

    print("\nRESPUESTA DEL AGENTE:")
    print(final_result)

    # Exportación si se solicitaron flags
    if parsed_args.export_md:
        agent.export_to_markdown(final_result, parsed_args.export_md)
    if parsed_args.export_pdf:
        agent.export_to_pdf(final_result, parsed_args.export_pdf)
    if parsed_args.keep_intermediate:
        print(f"Archivos intermedios conservados en: {agent.output_dir}")
