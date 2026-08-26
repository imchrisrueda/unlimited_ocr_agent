# Architecture

Status: active

## System context
Herramienta local de terminal (CLI) para convertir cuadernos de campo (PDF/imágenes) en Markdown estructurado mediante visión artificial local (Unlimited-OCR y Qwen 3.5 9B vía LM Studio).

## Components and boundaries
- **CLI/Pipeline** (`src/fieldnotes/cli.py`, `pipeline.py`): Orquesta el flujo de trabajo.
- **Ingesta** (`src/fieldnotes/ingest/pdf.py`): Convierte PDF a imágenes.
- **OCR** (`src/fieldnotes/ocr/`): Extrae texto raw como hipótesis.
- **VLM Client** (`src/fieldnotes/vlm/lmstudio.py`): Capa de comunicación con LM Studio.
- **Schemas & Validation** (`src/fieldnotes/schemas/`, `validation/`): Define y valida Pydantic models (ej: EstadilloRow).
- **Renderer** (`src/fieldnotes/render/markdown.py`): Generador determinista de Markdown desde IR.

## Data flow
1. PDF -> Ingesta -> Imágenes por página.
2. Imágenes -> Worker OCR -> OCR raw por página.
3. Descarga de modelo OCR para liberar memoria GPU.
4. Imagen + OCR raw -> VLM (LM Studio) -> JSON (EstadilloPage).
5. Estructuras por página -> Fusión (Python) -> Validación.
6. Estructura validada (DocumentIR) -> Renderer -> Salidas (Markdown final, Document JSON, Crops/Imágenes).

## Interfaces
- Interfaz CLI retrocompatible (`agent.py`).
- API Python retrocompatible (`UnlimitedOCRAgent`).
- LM Studio API (Local OpenAI-compatible API).

## Storage
- Salida en subdirectorios estructurados:
  - `notebook.md`
  - `document.json`
  - `pages/`
  - `raw/`
  - `assets/`
  - `review/`

## External systems
- LM Studio (vía HTTP localhost).
- baidu/Unlimited-OCR.

## Deployment
Entorno virtual Python gestionado con `uv`. Script de setup (`setup_env.py`) para detectar y configurar soporte CUDA.

## Testing architecture
Tests unitarios portables ejecutados con `python -m unittest`. Diseño de tests para no depender de red, GPU ni LM Studio, posibilitando integración continua (CI) en cualquier hardware.

## Security
Procesamiento 100% offline. No se envían datos a la nube.

## Observability
Logs de CLI de progreso. Generación de `review/issues.json` y crops para regiones ambiguas o problemáticas (trazabilidad de evidencia).

## Trade-offs
Separación de fases (OCR y luego VLM) implica mayor tiempo de ejecución pero evita los OOM (Out Of Memory) en GPUs de 8GB.
No generar Markdown en VLM asegura trazabilidad a costa de añadir una capa de renderizado intermedia (DocumentIR).
