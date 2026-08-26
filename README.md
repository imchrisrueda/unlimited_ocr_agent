# Agente de OCR con Unlimited-OCR y LM Studio

Herramienta local para digitalizar documentos PDF e imágenes mediante el modelo multimodal `baidu/Unlimited-OCR`. Puede exportar el resultado a Markdown o PDF y, opcionalmente, enviarlo a LM Studio para análisis de texto o procesamiento visual multimodal.

Unlimited-OCR debe entenderse como un modelo de OCR y parsing visual de documentos. Su objetivo es reconocer texto, tablas, fórmulas y estructura en imágenes, incluso a lo largo de varias páginas. No es un modelo de resumen ni un compresor de contexto para sustituir a un LLM.

## Requisitos

- Python 3.10 a 3.12
- `uv`
- LM Studio, para consultas y procesamiento posterior al OCR (texto o visión con `Qwen 3.5 9B`)
- GPU NVIDIA compatible, si se desea aceleración CUDA

## Instalación

```powershell
uv python install 3.11
uv venv .venv --python 3.11
.\.venv\Scripts\python.exe setup_env.py
uv pip install -r requirements.txt -p .venv
```

El configurador detecta automáticamente una GPU NVIDIA. Con GPU instala PyTorch con CUDA 12.4; en CPU instala la variante estándar.

En Windows, `setup_env.py` debe ejecutarse con la ruta de Python del entorno virtual para evitar depender de los alias de Microsoft Store.

## Uso

Convertir un documento a Markdown usando únicamente el OCR:

```powershell
.\.venv\Scripts\python.exe agent.py documento.pdf --raw --export-md resultado.md
```

La opción `--raw` devuelve directamente el Markdown generado por Unlimited-OCR, sin consultar LM Studio. Para una imagen se utiliza `infer` con `save_results=True`. Para un PDF, el agente rasteriza las páginas a 300 DPI en `pages/page_001.png` y utiliza `infer_multi`, que es el flujo recomendado por el proyecto oficial para parsing multipágina. Se activan los parámetros de prevención de repetición recomendados por el modelo (`no_repeat_ngram_size=35` y `ngram_window=1024` en documentos multipágina).

Ejemplo con el documento incluido en este repositorio:

```powershell
.\.venv\Scripts\python.exe agent.py 26-05-06.pdf --raw --export-md 26-05-06_raw.md
```

Procesar el texto con LM Studio:

```powershell
.\.venv\Scripts\python.exe agent.py documento.pdf "Resume las obligaciones y las fechas importantes" --export-md resumen.md
```

### Modo Multimodal (Visión + OCR)

Para que el modelo VLM (`Qwen 3.5 9B`) consulte directamente la imagen como fuente primaria de evidencia y utilice el OCR como contexto complementario:

```powershell
.\.venv\Scripts\python.exe agent.py 26-05-06.pdf --ask-vision --vision-model "qwen/qwen3.5-9b" --export-md 26-05-06_vision.md
```

En documentos PDF, `--ask-vision` procesa el OCR en subproceso aislado, libera la GPU, y posteriormente procesa de forma secuencial cada página enviando la imagen y su OCR correspondiente a LM Studio.

También puedes mantener la instrucción fuera del código y cambiarla entre ejecuciones:

```powershell
.\.venv\Scripts\python.exe agent.py documento.pdf --instruction-file AGENTS.md --export-md resultado.md
```

Si se proporcionan una instrucción posicional y `--instruction-file`, prevalece el contenido del archivo. El agente no carga `AGENTS.md` automáticamente: la instrucción se elige de forma explícita para que el mismo OCR pueda reutilizarse con tareas diferentes.

Para usar LM Studio, inicia su servidor local en `http://localhost:1234`. La URL y la clave pueden configurarse mediante `LM_STUDIO_URL` y `LM_STUDIO_API_KEY`. LM Studio se utiliza después del OCR para resumir, consultar o transformar el texto reconocido; no participa en la extracción raw.

También se puede exportar a PDF con `--export-pdf` o generar ambas salidas en una sola ejecución.

## Estructura

- `agent.py`: wrapper y entrypoint retrocompatible.
- `src/fieldnotes/`: código modularizado:
  - `artifacts.py`: estructura `PageArtifact` y estado auditable (`pending`, `mapped`, `unaligned`).
  - `ingest/pdf.py`: rasterizado de páginas a `pages/page_001.png` y wrappers compatibles.
  - `ocr/unlimited.py`: inferencia OCR, particionado determinista de bloques `<PAGE>` y persistencia en `raw/`.
  - `ocr/worker.py`: subproceso aislado de inferencia OCR, protocolo IPC determinista y excepciones tipadas.
  - `vlm/lmstudio.py`: cliente multimodal para LM Studio (`ask_text`, `ask_vision`), codificación Data URI y excepciones tipadas.
  - `pipeline.py`: orquestador `UnlimitedOCRAgent` con soporte de `ocr_mode` (`worker` e `in_process`) y visión.
  - `config.py`: configuración de entorno, modelos de visión/texto, timeout del worker y codificación.
  - `export.py`: exportación a Markdown y PDF.
  - `cli.py`: interfaz de línea de comandos con soporte `--ask-vision`, `--vision-model` y `--text-model`.
- `setup_env.py`: detección de hardware e instalación de PyTorch.
- `requirements.txt`: dependencias Python.
- `26-05-06.pdf`: documento de prueba incluido en el repositorio.

## Arquitectura de ejecución y liberación de VRAM

En sistemas con 12 GB de VRAM (NVIDIA RTX 4070 Ti), `Unlimited-OCR` y `Qwen 3.5 9B` (vía LM Studio) se ejecutan de forma estrictamente secuencial:
1. **Rasterizado**: El proceso padre convierte el PDF a imágenes en `pages/page_001.png...`.
2. **Inferencia OCR aislada**: Por defecto (`ocr_mode="worker"`), se lanza un subproceso hijo independiente mediante `subprocess` (`shell=False`).
3. **Liberación de VRAM**: Al completar la extracción, el proceso worker finaliza y el sistema operativo reclama la memoria CUDA. El proceso padre nunca importa `torch` ni `transformers` en este modo.
4. **Procesamiento VLM Multimodal**: El proceso padre lee el texto extraído e interactúa con LM Studio (`ask_text` o `ask_vision`) con la memoria de GPU totalmente libre.

### Protocolo IPC determinista
La comunicación entre padre e hijo se gestiona dentro del subdirectorio temporal del run en `output_ocr/run_xxx/ipc/`:
- `worker_request_<id>.json`: parámetros de entrada con un `request_id` único e impredecible.
- `worker_response_<id>.json`: metadatos de respuesta (`result_path`, estado de mapeo y errores) sin duplicar `raw_text` en el JSON, escrito mediante reemplazo atómico (`os.replace`).
- `worker_<id>_stdout.log` y `worker_<id>_stderr.log`: captura y persistencia completa de flujos de salida del worker.

### Modos de ejecución (`ocr_mode`)
- `--ocr-mode worker` (por defecto): Inferencia en subproceso aislado. Las propiedades del modelo PyTorch (`agent.model`, `agent.tokenizer`, `agent.device`, `agent.dtype`) no están instanciadas en el proceso padre y su acceso lanza un `RuntimeError` informativo.
- `--ocr-mode in_process`: Ejecuta `UnlimitedOCR` directamente en el proceso principal, preservando la delegación de acceso a las propiedades del modelo para desarrollo o depuración.
- `--worker-timeout <segundos>` / `OCR_WORKER_TIMEOUT`: Tiempo límite de espera para el subproceso OCR (por defecto 1800 s para permitir la descarga inicial del modelo).

## Uso adecuado y límites

- Para imágenes individuales: `infer` con `<image>document parsing.` y `save_results=True`.
- Para PDF o documentos multipágina: convertir las páginas a imágenes y usar `infer_multi` con `<image>Multi page parsing.`.
- El resultado OCR puede contener HTML de tablas, coordenadas, etiquetas de detección o errores de reconocimiento. Debe validarse antes de usarlo como dato estructurado.
- La conversión a PDF de este proyecto es una exportación visual del texto Markdown; no reconstruye automáticamente un PDF editable con el diseño original.

La implementación se basa en la [documentación oficial de Unlimited-OCR](https://github.com/baidu/Unlimited-OCR), que distingue explícitamente entre `infer` para una imagen y `infer_multi` para varias páginas.

## Archivos intermedios y artefactos por página

Durante la inferencia se generan artefactos estructurados dentro del subdirectorio temporal del run en `output_ocr`:
- `pages/`: imágenes rasterizadas de cada página (`page_001.png`, `page_002.png`, ...).
- `raw/`:
  - `document.md`: copia textual exacta e inalterada de `result.md`.
  - `page_001.md`, `page_002.md`, ...: OCR de cada página cuando el mapeo de bloques `<PAGE>` es unívoco y exacto (`ocr_mapping_status="mapped"`).
- `ipc/`: archivos de petición, respuesta atómica y logs correlacionados por `request_id`.
- `result.md` y cajas de detección (`result_with_boxes_N.jpg`).

Si el conteo de bloques difiere o existe texto antes del primer delimitador `<PAGE>`, el mapeo se declara `unaligned` con registro de error auditable y se conserva únicamente `raw/document.md`, evitando asignaciones parciales o erróneas.

Por defecto, el agente elimina el subdirectorio temporal al finalizar. Para conservarlo durante una ejecución concreta:

```powershell
.\.venv\Scripts\python.exe agent.py documento.pdf --raw --export-md resultado.md --keep-intermediate
```

Las rutas indicadas mediante `--export-md` y `--export-pdf` se conservan; solo se limpia el subdirectorio temporal de esa ejecución.

## Configuración de Modelos en LM Studio

La resolución de modelos sigue un orden de precedencia determinista y estricto:
1. Argumento específico por llamada en `ask_text(model=...)` o `ask_vision(model=...)`.
2. Opciones de CLI `--vision-model` y `--text-model` (o constructor `vision_model` / `text_model`).
3. Variables de entorno específicas `LM_STUDIO_VISION_MODEL` y `LM_STUDIO_TEXT_MODEL`.
4. Opciones CLI legacy `--lm-model` (o constructor `default_model` / `lm_model`).
5. Variable de entorno legacy `LM_STUDIO_MODEL`.

Ejemplo de configuración por entorno:

```powershell
$env:LM_STUDIO_VISION_MODEL="qwen/qwen3.5-9b"
$env:LM_STUDIO_TEXT_MODEL="qwen/qwen3.5-9b"
python agent.py documento.pdf --ask-vision --export-md respuesta.md
```

Si no se especifica ningún modelo para la operación solicitada, el sistema lanza `LMStudioModelNotConfiguredError` indicando en el diagnóstico los modelos anunciados por `/v1/models`.

### Razonamiento y documentos extensos

Puedes activar razonamiento cuando la tarea requiera comparar, inferir o elaborar un resumen analítico:

```powershell
python agent.py documento.pdf "Realiza un resumen analítico con conclusiones y evidencias" --reasoning-effort medium --max-tokens 4096 --export-md resumen.md
```

Los niveles disponibles son `none`, `low`, `medium` y `high`. `--max-tokens` limita la respuesta generada; no aumenta la ventana de contexto del modelo.

Para documentos que no caben cómodamente en una sola petición, activa el procesamiento por fragmentos:

```powershell
python agent.py documento.pdf "Realiza un resumen analítico global" --reasoning-effort medium --max-tokens 2048 --chunk-size 12000 --chunk-overlap 500 --export-md resumen.md
```

El agente analiza cada fragmento y realiza una última llamada de síntesis. `--chunk-size` y `--chunk-overlap` se expresan en caracteres. Este modo aumenta el tiempo y el número de llamadas, pero evita depender de una única ventana de contexto para documentos extensos.

Para consultas posteriores a OCR, el agente envía `reasoning_effort="none"` por defecto. Esto evita que modelos como Gemma consuman todo el límite de salida en `reasoning_content` y devuelvan `content` vacío. Si LM Studio devuelve una respuesta sin contenido, el terminal muestra una ayuda indicando esta causa y las alternativas: usar un modelo no razonador o aumentar el límite de tokens.

## Tests y Validación

Ejecución de la suite completa de tests unitarios offline (100% mocked, sin dependencias de red ni servidor):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### Pruebas de integración reales (opt-in)

1. **Integración con Unlimited-OCR y liberación de VRAM** (requiere GPU CUDA y archivo `26-05-06.pdf`):
```powershell
$env:RUN_OCR_WORKER_INTEGRATION="1"
.\.venv\Scripts\python.exe -m unittest tests.test_ocr.TestRealOCRWorkerIntegration.test_real_ocr_worker_pipeline_on_pdf -v
```

2. **Integración multimodal con LM Studio / Qwen 3.5 9B** (requiere LM Studio activo en `http://localhost:1234`):
```powershell
$env:RUN_LMSTUDIO_VISION_INTEGRATION="1"
$env:LM_STUDIO_VISION_MODEL="qwen/qwen3.5-9b"
.\.venv\Scripts\python.exe -m unittest tests.test_vlm.TestRealLMStudioVisionIntegration.test_real_lmstudio_vision_query -v
```

3. **Integración estructurada con LM Studio (Structured Outputs con Pydantic)**:
```powershell
$env:RUN_LMSTUDIO_STRUCTURED_INTEGRATION="1"
$env:LM_STUDIO_VISION_MODEL="qwen/qwen3.5-9b"
.\.venv\Scripts\python.exe -m unittest tests.test_structured.TestRealLMStudioStructuredIntegration.test_real_lmstudio_structured_query -v
```

## Esquemas Tipados y Structured Outputs

El módulo `src/fieldnotes/schemas/` introduce esquemas Pydantic v2 (`pydantic>=2.12.0,<3.0.0`) con política estricta (`strict=True, extra="forbid"`):
- `EvidenceValue[T]`: contenedor genérico para preservar el valor crudo (`raw`), el valor tipado (`normalized`), la página fuente (`source_page >= 1`), la incertidumbre (`uncertain`) y lecturas alternativas (`alternatives`).
- `ExtractionWarning`: representación tipada de advertencias con campo `details` restringido a tipos JSON.
- `BlockIR`, `PageIR`, `DocumentIR`: representación intermedia independiente del formato de salida.
- `EstadilloHeader`, `EstadilloRow`, `EstadilloPage`, `EstadilloDocument`: contratos tipados para digitalización estructurada de notas de campo.

Para consultas estructuradas directas:
```python
from src.fieldnotes.vlm.lmstudio import LMStudioClient
from src.fieldnotes.schemas.estadillo import EstadilloPage

client = LMStudioClient(base_url="http://localhost:1234/v1", api_key="lm-studio", vision_model="qwen/qwen3.5-9b")
pagina: EstadilloPage = client.ask_vision_structured(
    image_path="page_001.png",
    prompt="Extrae la tabla de notas de campo visible en la imagen.",
    schema=EstadilloPage,
)
```

## Perfil de Dominio `estadillo`

El perfil `--profile estadillo` implementa el flujo integral de digitalización de cuadernos de campo:
1. **Inferencia OCR aislada**: Procesa el PDF/imagen con `Unlimited-OCR` en worker independiente y libera la VRAM.
2. **Extracción estructurada con VLM**: Realiza consultas visuales secuenciales a `Qwen 3.5 9B` página a página extrayendo objetos `EstadilloPage`.
3. **Fusión determinista multipágina** (`src/fieldnotes/merge/estadillo.py`): Ordena páginas y reconcilia la cabecera sin sobreescrituras silenciosas (emitiendo `HEADER_CONFLICT` ante discrepancias).
4. **Normalización auditable de especies** (`src/fieldnotes/normalization/estadillo.py`): Mapea variantes de catálogo (`Ap` $\rightarrow$ `P`, `Ah` $\rightarrow$ `H`, `Ar` $\rightarrow$ `R`, `Mz` $\rightarrow$ `M`) en `especie.normalized` conservando `especie.raw` inalterado. Valores no reconocidos o ambiguos (como `M2`) generan `UNRECOGNIZED_SPECIES` sin auto-corrección destructiva.
5. **Validación de calidad** (`src/fieldnotes/validation/estadillo.py`): Detecta duplicados `(col, fil)`, discontinuidades en secuencias de filas, formatos BBCH inválidos y anomalías de altura emitiendo `ExtractionWarning`.
6. **Renderizado Markdown determinista** (`src/fieldnotes/render/markdown.py`): Genera exactamente las **dos tablas Markdown obligatorias de `AGENTS.md`** (tabla 1: 3x2 con metadatos y `Especies: P;H;R;M` fija; tabla 2: 8 columnas con todos los registros en orden de procedencia).
7. **Sistema de revisión y crops visuales** (`src/fieldnotes/review/`): Agrega de forma pura y determinista las incertidumbres (`EvidenceValue.uncertain`, `alternatives`) y advertencias (`ExtractionWarning`) en objetos `ReviewIssue`, generando recortes visuales PNG confinados en `review/` únicamente cuando existe una región de evidencia normalizada válida.
8. **Persistencia canónica atómica**: Guarda los resultados en `<output_dir>/<safe_stem>/` (`notebook.md`, `document.json`, `pages/`, `raw/`, `assets/`, `review/`).

## Sistema de Revisión: `ReviewIssue`, `issues.json` y Crops Visuales

El sistema de revisión hace visibles y auditables las dudas o discrepancias detectadas durante la digitalización sin modificar los datos extraídos ni realizar auto-corrección destructiva:

### Esquema `ReviewIssue` (`src/fieldnotes/schemas/review.py`)
- `issue_id`: Identificador determinista, único y estable entre ejecuciones idénticas (sin UUID aleatorio ni timestamps).
- `page`: Número de página fuente ($\ge 1$).
- `field`: Campo afectado (ej: `especie`, `altura_cm`, `bbch`, `col,fil`, `objetivo`).
- `row_key`: Clave textual conservadora derivada de las coordenadas o identificadores disponibles (ej: `col1_fil26`, `id_43`) o `null`.
- `reason`: Motivo descriptivo de la incertidumbre o advertencia.
- `candidates`: Lecturas o valores alternativos procedentes de `alternatives` o de la evidencia explícita (sin candidatos inventados).
- `crop_path`: Ruta relativa POSIX (`review/p001_col1_fil26_altura_cm.png`) al recorte visual PNG de la celda o fila dudosa, o `null`.
- `warning_code`: Código formal de la advertencia (`UNCERTAIN_EVIDENCE`, `UNRECOGNIZED_SPECIES`, `INVALID_BBCH_FORMAT`, `DUPLICATE_COORDINATES`, etc.).
- `provenance`: Metadatos auditables de severidad y detalle original.

### Significado de `crop_path: null`
Un recorte PNG solo se genera cuando el DTO de extracción multimodal aporta una región visual explícita y válida (`NormalizedBBox` con $0.0 \le x_0 < x_1 \le 1.0$ e $0.0 \le y_0 < y_1 \le 1.0$). Si una advertencia o incertidumbre carece de coordenadas visuales válidas o afecta a la lógica global del documento, `crop_path` queda explícitamente como `null`. El sistema nunca genera coordenadas inventadas ni recortes aproximados engañosos.

### Archivo `review/issues.json`
Se crea siempre de forma atómica en el directorio canónico del documento. Si el documento no presenta ninguna incertidumbre ni advertencia, `review/issues.json` se persiste válidamente con una lista vacía `[]`.

### Ejecución CLI con Perfil Estadillo e Informe de Revisión

```powershell
.\.venv\Scripts\python.exe agent.py 26-05-06.pdf --profile estadillo --vision-model "qwen/qwen3.5-9b" --output ./output_ocr
```

Al finalizar, la CLI informa de forma concisa sobre los registros extraídos, las advertencias detectadas y el número de elementos que requieren revisión humana:
```text
[PERFIL ESTADILLO] Procesamiento completado: 156 registros extraídos, 3 advertencias detectadas, 3 elementos que requieren revisión.
```

### Prueba de integración real E2E (opt-in)

Procesamiento completo de las 6 páginas de `26-05-06.pdf` con OCR worker y Qwen 3.5 9B:

```powershell
$env:RUN_ESTADILLO_INTEGRATION="1"
$env:LM_STUDIO_VISION_MODEL="qwen/qwen3.5-9b"
.\.venv\Scripts\python.exe -m unittest tests.test_profile_estadillo.TestRealEstadilloProfileIntegration.test_real_estadillo_e2e_on_pdf -v
```
