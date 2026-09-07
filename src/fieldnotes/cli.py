import argparse
import json
import os
from pathlib import Path
from typing import Optional
from .pipeline import UnlimitedOCRAgent
from .config import (
    get_lm_studio_vision_model,
    get_lm_studio_text_model,
    get_lm_studio_legacy_model,
    NotebookConfig,
)
from .profiles.estadillo import safe_document_stem


def build_parser():
    parser = argparse.ArgumentParser(description="Agente IA: Unlimited-OCR + LM Studio")
    parser.add_argument("file_path", help="Ruta de la imagen o archivo PDF a digitalizar")
    parser.add_argument(
        "--profile",
        choices=("default", "estadillo", "notebook", "cuaderno_campo"),
        default="default",
        help="Perfil de dominio: 'estadillo', 'cuaderno_campo', 'notebook' o 'default'",
    )
    parser.add_argument(
        "--config",
        help="Ruta al archivo JSON de configuración tipada para el perfil (p. ej. NotebookConfig)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Directorio base para la salida estructurada canónica (por defecto ./output_ocr)",
    )
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
        help="Identificador del modelo de LM Studio (legacy; mapea a texto y fallback de visión)",
    )
    parser.add_argument(
        "--vision-model",
        help="Identificador del modelo de visión multimodal de LM Studio (p. ej. Qwen 3.5 9B)",
    )
    parser.add_argument(
        "--text-model",
        help="Identificador del modelo de texto de LM Studio",
    )
    parser.add_argument(
        "--ask-vision",
        action="store_true",
        help="Activa el análisis multimodal enviando la imagen original junto con el texto OCR",
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

    # Precedencia exacta de resolución de modelos en CLI:
    # 1. Flag específico CLI (--vision-model / --text-model)
    # 2. Variable de entorno específica (LM_STUDIO_VISION_MODEL / LM_STUDIO_TEXT_MODEL)
    # 3. Flag legacy CLI (--lm-model)
    # 4. Variable de entorno legacy (LM_STUDIO_MODEL)
    legacy_env = get_lm_studio_legacy_model()
    vision_model = (
        parsed_args.vision_model
        or get_lm_studio_vision_model()
        or parsed_args.lm_model
        or legacy_env
    )
    text_model = (
        parsed_args.text_model
        or get_lm_studio_text_model()
        or parsed_args.lm_model
        or legacy_env
    )

    # Pre-validación de --config
    if parsed_args.config and parsed_args.profile not in ("notebook", "cuaderno_campo"):
        parser.error("--config solo es compatible con --profile notebook")

    # Validaciones específicas de --profile estadillo ANTES de instanciar agente u OCR
    if parsed_args.profile == "estadillo":
        if parsed_args.raw:
            parser.error("--raw no es compatible con --profile estadillo")
        if parsed_args.ask_vision:
            parser.error("--ask-vision no es compatible con --profile estadillo")
        if parsed_args.chunk_size > 0:
            parser.error("--chunk-size no es compatible con --profile estadillo")
        if parsed_args.export_md:
            parser.error(
                "--export-md no es compatible con --profile estadillo; "
                "la salida canónica se persiste automáticamente en <output>/<fecha>/notas.md y datos.csv"
            )
        if parsed_args.export_pdf:
            parser.error("--export-pdf no es compatible con --profile estadillo")
        if not vision_model:
            parser.error(
                "Para usar --profile estadillo debe especificarse un modelo de visión mediante "
                "--vision-model, la variable LM_STUDIO_VISION_MODEL, --lm-model o la variable LM_STUDIO_MODEL."
            )

        agent = UnlimitedOCRAgent(
            vision_model=vision_model,
            text_model=text_model,
            lm_model=parsed_args.lm_model or legacy_env,
            output_dir=parsed_args.output or "./output_ocr",
            ocr_mode=parsed_args.ocr_mode,
            worker_timeout=parsed_args.worker_timeout,
        )
        if not parsed_args.keep_intermediate:
            import atexit

            atexit.register(agent.cleanup)

        custom_prompt = (
            instruction
            if parsed_args.instruction_file
            or parsed_args.prompt
            != "Digitaliza este documento manteniendo su estructura en Markdown limpio."
            else None
        )

        effective_max_tokens = (
            4096 if parsed_args.max_tokens == 2048 else parsed_args.max_tokens
        )
        final_result, doc_result = agent.process_estadillo(
            file_path=parsed_args.file_path,
            output_dir=parsed_args.output,
            prompt=custom_prompt,
            max_tokens=effective_max_tokens,
            reasoning_effort=parsed_args.reasoning_effort,
        )

        total_rows = sum(len(p.rows) for p in doc_result.pages)
        total_warnings = len(doc_result.warnings)

        # Contar elementos que requieren revisión desde review/issues.json
        review_issues_path = (
            Path(parsed_args.output or "./output_ocr")
            / safe_document_stem(parsed_args.file_path)
            / "review"
            / "issues.json"
        )
        total_review_items = 0
        if review_issues_path.is_file():
            try:
                issues_data = json.loads(review_issues_path.read_text(encoding="utf-8"))
                if isinstance(issues_data, list):
                    total_review_items = len(issues_data)
            except Exception:
                total_review_items = 0

        print(
            f"\n[PERFIL ESTADILLO] Procesamiento completado: {total_rows} registros extraídos, "
            f"{total_warnings} advertencias detectadas, "
            f"{total_review_items} elementos que requieren revisión."
        )

        print("\nRESPUESTA DEL AGENTE:")
        print(final_result)

        if parsed_args.keep_intermediate:
            print(f"Archivos intermedios conservados en: {agent.output_dir}")

        return

    # Validaciones de los perfiles notebook y cuaderno_campo antes de iniciar OCR
    if parsed_args.profile in ("notebook", "cuaderno_campo"):
        if parsed_args.raw:
            parser.error(f"--raw no es compatible con --profile {parsed_args.profile}")
        if parsed_args.ask_vision:
            parser.error(f"--ask-vision no es compatible con --profile {parsed_args.profile}")
        if parsed_args.chunk_size > 0:
            parser.error(f"--chunk-size no es compatible con --profile {parsed_args.profile}")
        if parsed_args.export_md:
            parser.error(
                f"--export-md no es compatible con --profile {parsed_args.profile}; "
                "la salida Markdown canónica se persiste automáticamente dentro de <output>/<documento>/"
            )
        if parsed_args.export_pdf:
            parser.error(f"--export-pdf no es compatible con --profile {parsed_args.profile}")
        if not vision_model:
            parser.error(
                f"Para usar --profile {parsed_args.profile} debe especificarse un modelo de visión mediante "
                "--vision-model, la variable LM_STUDIO_VISION_MODEL, --lm-model o la variable LM_STUDIO_MODEL."
            )

        notebook_config = None
        if parsed_args.config:
            try:
                notebook_config = NotebookConfig.from_file(parsed_args.config)
            except Exception as cfg_exc:
                parser.error(f"Error al cargar --config '{parsed_args.config}': {cfg_exc}")

        agent = UnlimitedOCRAgent(
            vision_model=vision_model,
            text_model=text_model,
            lm_model=parsed_args.lm_model or legacy_env,
            output_dir=parsed_args.output or "./output_ocr",
            ocr_mode=parsed_args.ocr_mode,
            worker_timeout=parsed_args.worker_timeout,
        )
        if not parsed_args.keep_intermediate:
            import atexit

            atexit.register(agent.cleanup)

        custom_prompt = (
            instruction
            if parsed_args.instruction_file
            or parsed_args.prompt
            != "Digitaliza este documento manteniendo su estructura en Markdown limpio."
            else None
        )

        effective_max_tokens = (
            4096 if parsed_args.max_tokens == 2048 else parsed_args.max_tokens
        )
        final_result, doc_result = (agent.process_cuaderno_campo if parsed_args.profile == "cuaderno_campo" else agent.process_notebook)(
            file_path=parsed_args.file_path,
            output_dir=parsed_args.output,
            config=notebook_config,
            prompt=custom_prompt,
            max_tokens=effective_max_tokens,
            reasoning_effort=parsed_args.reasoning_effort,
        )

        total_pages = len(doc_result.pages)
        total_rows = len(doc_result.estadillo_rows)
        total_sections = len(doc_result.sections)
        total_tables = len(doc_result.tables)
        total_diagrams = len(doc_result.diagrams)
        total_warnings = len(doc_result.warnings)

        # Contar elementos que requieren revisión desde review/issues.json
        review_issues_path = (
            Path(parsed_args.output or "./output_ocr")
            / safe_document_stem(parsed_args.file_path)
            / "review"
            / "issues.json"
        )
        total_review_items = 0
        if review_issues_path.is_file():
            try:
                issues_data = json.loads(review_issues_path.read_text(encoding="utf-8"))
                if isinstance(issues_data, list):
                    total_review_items = len(issues_data)
            except Exception:
                total_review_items = 0

        if parsed_args.profile == "cuaderno_campo":
            summary = (
                f"\n[PERFIL CUADERNO_CAMPO] Procesamiento completado: {total_pages} páginas, "
                f"{total_sections} secciones, {total_tables} tablas generales, "
                f"{total_diagrams} figuras, {total_warnings} advertencias, "
                f"{total_review_items} elementos para revisión."
            )
        else:
            summary = (
                f"\n[PERFIL NOTEBOOK] Procesamiento completado: {total_pages} páginas, "
                f"{total_rows} filas de estadillo, {total_sections} secciones, "
                f"{total_tables} tablas, {total_diagrams} diagramas, "
                f"{total_warnings} advertencias, {total_review_items} elementos para revisión."
            )
        print(summary)

        print("\nRESPUESTA DEL AGENTE:")
        print(final_result)

        if parsed_args.keep_intermediate:
            print(f"Archivos intermedios conservados en: {agent.output_dir}")

        return

    # Modo default (retrocompatible)
    if parsed_args.ask_vision and not vision_model:
        parser.error(
            "Para usar --ask-vision debe especificarse un modelo de visión mediante "
            "--vision-model, la variable LM_STUDIO_VISION_MODEL, --lm-model o la variable LM_STUDIO_MODEL."
        )

    agent = UnlimitedOCRAgent(
        vision_model=vision_model,
        text_model=text_model,
        lm_model=parsed_args.lm_model or legacy_env,
        output_dir=parsed_args.output or "./output_ocr",
        ocr_mode=parsed_args.ocr_mode,
        worker_timeout=parsed_args.worker_timeout,
    )
    if not parsed_args.keep_intermediate:
        import atexit

        atexit.register(agent.cleanup)

    is_pdf = parsed_args.file_path.lower().endswith(".pdf")
    if is_pdf:
        raw_ocr_text = agent.extract_from_pdf(parsed_args.file_path)
    else:
        raw_ocr_text = agent.extract_from_image(parsed_args.file_path)

    print("\n--- TEXTO EXTRAÍDO POR UNLIMITED-OCR (Vista previa) ---")
    print(raw_ocr_text[:500] + ("..." if len(raw_ocr_text) > 500 else ""))
    print("-------------------------------------------------------\n")

    if parsed_args.raw:
        final_result = raw_ocr_text
    elif parsed_args.ask_vision:
        print("Consultando a LM Studio en modo multimodal (Visión + OCR)...")
        if is_pdf:
            page_results = []
            for artifact in agent.page_artifacts:
                page_text = agent.ask_page_vision(
                    artifact,
                    prompt=instruction,
                    reasoning_effort=parsed_args.reasoning_effort,
                    max_tokens=parsed_args.max_tokens,
                )
                page_results.append(f"### Página {artifact.page_number}\n{page_text}")
            final_result = "\n\n".join(page_results)
        else:
            final_result = agent.ask_vision(
                image_path=parsed_args.file_path,
                prompt=instruction,
                ocr_context=raw_ocr_text,
                reasoning_effort=parsed_args.reasoning_effort,
                max_tokens=parsed_args.max_tokens,
            )
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