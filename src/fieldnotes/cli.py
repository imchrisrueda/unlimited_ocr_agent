import argparse
import json
import os
import sys
import tempfile
from contextlib import ExitStack
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
from .progress import PipelineProgress
from .ingest.images import prepare_input_source, convert_images_to_pdf, is_image_file


CLI_EPILOG = r"""
Ejemplos de uso:
  # Procesar estadillo agronómico o forestal desde PDF:
  python agent.py 26-05-06.pdf --profile estadillo

  # Procesar cuaderno de campo (se extrae texto y diagramas/croquis):
  python agent.py .\entrada\2026-06-26\notas_campo_2026-06-26.pdf --profile cuaderno_campo

  # Procesar cuaderno de campo priorizando solo texto limpio y ordenado (sin diagramas):
  python agent.py .\entrada\2026-06-26\notas_campo_2026-06-26.pdf --profile cuaderno_campo --no-diagrams

  # Procesar cuaderno de campo extrayendo diagramas solo en páginas específicas:
  python agent.py .\entrada\2026-06-26\notas_campo_2026-06-26.pdf --profile cuaderno_campo --diagram-pages 2

  # Procesar fotos de una carpeta como cuaderno de campo (se convierte automáticamente a PDF):
  python agent.py .\entrada\2026-09-14\ --profile cuaderno_campo

  # Procesar lista de fotos específicas combinadas en un único PDF:
  python agent.py --images foto1.png foto2.png --profile cuaderno_campo

  # Liberar inmediatamente toda la memoria VRAM y descargar todos los modelos en LM Studio:
  python agent.py --unload-all

  # Extracción visual multimodal directa sobre una imagen:
  python agent.py page.png --ask-vision --vision-model qwen/qwen3.5-9b

  # Digitalización directa con Unlimited-OCR (sin LLM):
  python agent.py documento.pdf --raw
"""


def build_parser():
    parser = argparse.ArgumentParser(
        description="Agente IA: Unlimited-OCR + LM Studio para digitalización de notas de campo.",
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "file_path",
        nargs="?",
        default=None,
        help="Ruta del archivo PDF, imagen o carpeta con imágenes a digitalizar",
    )
    parser.add_argument(
        "--images",
        nargs="+",
        help="Lista de rutas de imágenes individuales para combinar y procesar como un único documento PDF",
    )
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
        "--no-diagrams",
        "--text-only",
        action="store_true",
        dest="no_diagrams",
        help="Desactiva la extracción e inferencia de diagramas/croquis; prioriza la transcripción de texto limpio y ordenado",
    )
    parser.add_argument(
        "--diagram-pages",
        type=str,
        default=None,
        help="Lista de páginas específicas (separadas por coma, p. ej. '1,3' o '2') donde extraer diagramas/croquis",
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
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Desactiva la barra de progreso en el terminal",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Modo silencioso: reduce mensajes informativos y silencia la barra de carga",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Modo detallado: muestra información adicional de diagnóstico",
    )
    parser.add_argument(
        "--keep-models-loaded",
        action="store_true",
        help="No descarga el modelo VLM de LM Studio al completar la digitalización (por defecto se descarga para liberar VRAM)",
    )
    parser.add_argument(
        "--unload-all",
        action="store_true",
        help="Descarga inmediatamente todos los modelos cargados en LM Studio y libera la memoria VRAM/RAM sin procesar ningún documento",
    )
    return parser


def main(args=None):
    with ExitStack() as resources:
        return _main(args, resources)


def _main(args, resources):
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    if parsed_args.unload_all:
        from .vlm.lmstudio import LMStudioClient
        from .config import get_lm_studio_url, get_lm_studio_api_key
        client = LMStudioClient(base_url=get_lm_studio_url(), api_key=get_lm_studio_api_key(), validate_model=False)
        print("Descargando todos los modelos de LM Studio y liberando memoria VRAM/RAM...")
        unloaded = client.unload_all_models()
        if unloaded:
            print(f"Modelos descargados exitosamente de LM Studio: {', '.join(unloaded)}")
        else:
            print("No se detectaron modelos cargados o se completó la descarga global.")
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
        except Exception:
            pass
        return

    if not parsed_args.file_path and not parsed_args.images:
        parser.error("Debe especificarse 'file_path' o la opción '--images'")

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

    # Pre-validación de flags específicos de cuaderno
    if parsed_args.config and parsed_args.profile not in ("notebook", "cuaderno_campo"):
        parser.error("--config solo es compatible con --profile notebook o cuaderno_campo")

    if (parsed_args.no_diagrams or parsed_args.diagram_pages) and parsed_args.profile not in ("notebook", "cuaderno_campo"):
        parser.error("--no-diagrams y --diagram-pages solo son compatibles con los perfiles 'cuaderno_campo' y 'notebook'")

    parsed_diagram_pages: Optional[list[int]] = None
    if parsed_args.diagram_pages:
        try:
            parsed_diagram_pages = [int(p.strip()) for p in parsed_args.diagram_pages.split(",") if p.strip()]
            for p in parsed_diagram_pages:
                if p < 1:
                    raise ValueError(f"Los números de página deben ser enteros >= 1, obtenido: {p}")
            if not parsed_diagram_pages:
                raise ValueError("La lista de páginas no puede estar vacía.")
        except Exception as exc:
            parser.error(f"Formato inválido para --diagram-pages: {exc}")

    # Iniciar barra de progreso (auto-detecta terminal interactiva o flag explícito)
    show_progress = None if (not parsed_args.no_progress and not parsed_args.quiet) else False
    progress = PipelineProgress(
        total_phases=7 if parsed_args.profile in ("estadillo", "notebook", "cuaderno_campo") else 5,
        enabled=show_progress,
        desc="Digitalizando",
    )
    resources.callback(progress.close)
    input_workspace = resources.enter_context(tempfile.TemporaryDirectory(prefix="fieldnotes_input_"))

    # Preparación de entrada (carpetas de imágenes o lista de imágenes a PDF)
    target_input = parsed_args.file_path
    converted_pdf_path: Optional[Path] = None

    if parsed_args.images:
        progress.set_phase(1, "Preparando entrada", f"Combinando {len(parsed_args.images)} imágenes en PDF...")
        stem = safe_document_stem(parsed_args.images[0])
        tmp_pdf = Path(input_workspace) / f"{stem}.pdf"
        try:
            converted_pdf_path = convert_images_to_pdf(parsed_args.images, tmp_pdf)
            target_input = str(converted_pdf_path)
        except Exception as exc:
            progress.close()
            parser.error(f"Error al convertir imágenes especificadas en --images: {exc}")
    elif parsed_args.file_path and os.path.isdir(parsed_args.file_path):
        progress.set_phase(1, "Preparando entrada", f"Convirtiendo carpeta '{Path(parsed_args.file_path).name}' a PDF...")
        try:
            tmp_pdf, was_converted, stem = prepare_input_source(parsed_args.file_path, working_dir=input_workspace)
            if was_converted:
                converted_pdf_path = tmp_pdf
                target_input = str(tmp_pdf)
        except Exception as exc:
            progress.close()
            parser.error(f"Error al procesar carpeta de entrada: {exc}")
    elif parsed_args.file_path and is_image_file(parsed_args.file_path) and parsed_args.profile in ("estadillo", "notebook", "cuaderno_campo"):
        # Para perfiles estructurados con imagen única, convertir a PDF previo
        progress.set_phase(1, "Preparando entrada", f"Convirtiendo imagen '{Path(parsed_args.file_path).name}' a PDF...")
        try:
            tmp_pdf, was_converted, stem = prepare_input_source(parsed_args.file_path, working_dir=input_workspace, force_pdf_conversion=True)
            if was_converted:
                converted_pdf_path = tmp_pdf
                target_input = str(tmp_pdf)
        except Exception as exc:
            progress.close()
            parser.error(f"Error al preparar imagen de entrada: {exc}")

    # Validaciones específicas de --profile estadillo ANTES de instanciar agente u OCR
    if parsed_args.verbose and not parsed_args.quiet:
        print(f"Diagnóstico: perfil={parsed_args.profile}, OCR={parsed_args.ocr_mode}, entrada={target_input}")
        print(f"Modelos: visión={vision_model or '(sin configurar)'}, texto={text_model or '(sin configurar)'}")

    if parsed_args.profile == "estadillo":
        if parsed_args.raw:
            progress.close()
            parser.error("--raw no es compatible con --profile estadillo")
        if parsed_args.ask_vision:
            progress.close()
            parser.error("--ask-vision no es compatible con --profile estadillo")
        if parsed_args.chunk_size > 0:
            progress.close()
            parser.error("--chunk-size no es compatible con --profile estadillo")
        if parsed_args.export_md:
            progress.close()
            parser.error(
                "--export-md no es compatible con --profile estadillo; "
                "la salida canónica se persiste automáticamente en <output>/<fecha>/notas.md y datos.csv"
            )
        if parsed_args.export_pdf:
            progress.close()
            parser.error("--export-pdf no es compatible con --profile estadillo")
        if not vision_model:
            progress.close()
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
            resources.callback(agent.cleanup)

        resources.callback(agent.unload_ocr if parsed_args.keep_models_loaded else agent.unload_all)

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
            file_path=target_input,
            output_dir=parsed_args.output,
            prompt=custom_prompt,
            max_tokens=effective_max_tokens,
            reasoning_effort=parsed_args.reasoning_effort,
            progress=progress,
        )

        total_rows = sum(len(p.rows) for p in doc_result.pages)
        total_warnings = len(doc_result.warnings)

        # Contar elementos que requieren revisión desde review/issues.json
        review_issues_path = (
            Path(parsed_args.output or "./output_ocr")
            / safe_document_stem(target_input)
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

        if not parsed_args.quiet:
            print(f"\nTiempo de ejecución: {progress.format_elapsed()}")

        if parsed_args.keep_intermediate:
            print(f"Archivos intermedios conservados en: {agent.output_dir}")

        progress.close()
        return

    # Validaciones de los perfiles notebook y cuaderno_campo antes de iniciar OCR
    if parsed_args.profile in ("notebook", "cuaderno_campo"):
        if parsed_args.raw:
            progress.close()
            parser.error(f"--raw no es compatible con --profile {parsed_args.profile}")
        if parsed_args.ask_vision:
            progress.close()
            parser.error(f"--ask-vision no es compatible con --profile {parsed_args.profile}")
        if parsed_args.chunk_size > 0:
            progress.close()
            parser.error(f"--chunk-size no es compatible con --profile {parsed_args.profile}")
        if parsed_args.export_md:
            progress.close()
            parser.error(
                f"--export-md no es compatible con --profile {parsed_args.profile}; "
                "la salida Markdown canónica se persiste automáticamente dentro de <output>/<documento>/"
            )
        if parsed_args.export_pdf:
            progress.close()
            parser.error(f"--export-pdf no es compatible con --profile {parsed_args.profile}")
        if not vision_model:
            progress.close()
            parser.error(
                f"Para usar --profile {parsed_args.profile} debe especificarse un modelo de visión mediante "
                "--vision-model, la variable LM_STUDIO_VISION_MODEL, --lm-model o la variable LM_STUDIO_MODEL."
            )

        notebook_config = None
        if parsed_args.config:
            try:
                notebook_config = NotebookConfig.from_file(parsed_args.config)
            except Exception as cfg_exc:
                progress.close()
                parser.error(f"Error al cargar --config '{parsed_args.config}': {cfg_exc}")

        if parsed_args.no_diagrams or parsed_diagram_pages is not None:
            if notebook_config is None:
                notebook_config = NotebookConfig(
                    extract_diagrams=not parsed_args.no_diagrams,
                    diagram_pages=parsed_diagram_pages,
                )
            else:
                updates = {}
                if parsed_args.no_diagrams:
                    updates["extract_diagrams"] = False
                if parsed_diagram_pages is not None:
                    updates["diagram_pages"] = parsed_diagram_pages
                notebook_config = notebook_config.model_copy(update=updates)

        agent = UnlimitedOCRAgent(
            vision_model=vision_model,
            text_model=text_model,
            lm_model=parsed_args.lm_model or legacy_env,
            output_dir=parsed_args.output or "./output_ocr",
            ocr_mode=parsed_args.ocr_mode,
            worker_timeout=parsed_args.worker_timeout,
        )
        if not parsed_args.keep_intermediate:
            resources.callback(agent.cleanup)

        resources.callback(agent.unload_ocr if parsed_args.keep_models_loaded else agent.unload_all)

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
        runner = agent.process_cuaderno_campo if parsed_args.profile == "cuaderno_campo" else agent.process_notebook
        final_result, doc_result = runner(
            file_path=target_input,
            output_dir=parsed_args.output,
            config=notebook_config,
            prompt=custom_prompt,
            max_tokens=effective_max_tokens,
            reasoning_effort=parsed_args.reasoning_effort,
            progress=progress,
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
            / safe_document_stem(target_input)
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

        if not parsed_args.quiet:
            print(f"\nTiempo de ejecución: {progress.format_elapsed()}")

        if parsed_args.keep_intermediate:
            print(f"Archivos intermedios conservados en: {agent.output_dir}")

        progress.close()
        return

    # Modo default (retrocompatible)
    if parsed_args.ask_vision and not vision_model:
        progress.close()
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
        resources.callback(agent.cleanup)

    resources.callback(agent.unload_ocr if parsed_args.keep_models_loaded else agent.unload_all)

    is_pdf = target_input.lower().endswith(".pdf")
    progress.set_phase(2, "Ingesta y rasterizado", "Extrayendo páginas..." if is_pdf else "Cargando imagen...")
    progress.set_phase(3, "Inferencia OCR", "Ejecutando Unlimited-OCR...")
    if is_pdf:
        raw_ocr_text = agent.extract_from_pdf(target_input)
    else:
        raw_ocr_text = agent.extract_from_image(target_input)

    print("\n--- TEXTO EXTRAÍDO POR UNLIMITED-OCR (Vista previa) ---")
    print(raw_ocr_text[:500] + ("..." if len(raw_ocr_text) > 500 else ""))
    print("-------------------------------------------------------\n")

    if parsed_args.raw:
        final_result = raw_ocr_text
        progress.finish("OCR completado")
    elif parsed_args.ask_vision:
        progress.set_phase(4, "Extracción VLM", "Consultando a LM Studio...")
        print("Consultando a LM Studio en modo multimodal (Visión + OCR)...")
        if is_pdf:
            page_results = []
            total_arts = len(agent.page_artifacts)
            for idx, artifact in enumerate(agent.page_artifacts):
                progress.update_substep(f"Página {artifact.page_number}/{total_arts}")
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
                image_path=target_input,
                prompt=instruction,
                ocr_context=raw_ocr_text,
                reasoning_effort=parsed_args.reasoning_effort,
                max_tokens=parsed_args.max_tokens,
            )
        progress.finish("Análisis multimodal completado")
    elif parsed_args.chunk_size > 0 and len(raw_ocr_text) > parsed_args.chunk_size:
        progress.set_phase(4, "Procesamiento LLM", "Resumiendo por fragmentos...")
        final_result = agent.ask_lmstudio_chunked(
            raw_ocr_text,
            instruction,
            chunk_size=parsed_args.chunk_size,
            chunk_overlap=parsed_args.chunk_overlap,
            reasoning_effort=parsed_args.reasoning_effort,
            max_tokens=parsed_args.max_tokens,
        )
        progress.finish("Completado")
    else:
        progress.set_phase(4, "Procesamiento LLM", "Consultando modelo...")
        final_result = agent.ask_lmstudio(
            raw_ocr_text,
            instruction,
            reasoning_effort=parsed_args.reasoning_effort,
            max_tokens=parsed_args.max_tokens,
        )
        progress.finish("Completado")

    print("\nRESPUESTA DEL AGENTE:")
    print(final_result)

    # Exportación si se solicitaron flags
    if parsed_args.export_md:
        agent.export_to_markdown(final_result, parsed_args.export_md)
    if parsed_args.export_pdf:
        agent.export_to_pdf(final_result, parsed_args.export_pdf)
    if parsed_args.keep_intermediate:
        print(f"Archivos intermedios conservados en: {agent.output_dir}")

    progress.close()
