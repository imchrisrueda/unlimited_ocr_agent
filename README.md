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

## Benchmark y Métricas Reproducibles para Estadillos

El módulo `src/fieldnotes/benchmark/` proporciona un sistema de evaluación cuantitativa **offline, determinista y auditable** para medir la calidad de extracción de notas de campo frente a verdad de referencia manual, antes de optimizar prompts o modelos.

### Preparación del Dataset Manual y Privacidad

Para evaluar de forma rigurosa los pipelines de extracción:
1. **Selección representativa:** Se recomienda preparar un conjunto de **20 a 30 páginas reales** que cubra la variabilidad del dominio:
   - Estadillos impresos / tipografiados fáciles.
   - Estadillos difíciles con tablas deterioradas o columnas divididas.
   - Texto y tablas puramente manuscritos con anotaciones cursivas.
   - Páginas mixtas con cabecera de metadatos, tabla y notas al pie.
   - Croquis de campo y esquemas (catalogados en PR 8 con `evaluate_structured=False`; su evaluación estructural se abordará en PR 9/10 con DiagramIR).
2. **Privacidad estricta:** Los documentos reales sensibles y PDFs con datos privados (incluyendo `26-05-06.pdf`) **no deben versionarse en repositorios públicos ni compartirse en Git**.
3. **Fixtures sintéticos anonimizados:** El repositorio incluye un dataset fixture mínimo y completamente sintético en `tests/fixtures/benchmark/` para verificación continua offline (`case_01_easy`, `case_02_hard`, `case_03_handwritten`, `case_04_mixed`, `case_05_sketch`).
   > [!IMPORTANT]
   > **Aviso sobre datos sintéticos:** Las predicciones y resultados versionados en los fixtures son **puramente sintéticos y artificiales**, diseñados para probar la infraestructura de ejecución, esquemas y determinismo en CI/CD offline. **NO demuestran ni pretenden demostrar superioridad empírica real de ningún modelo sobre otro**. La evaluación científica real requiere la anotación manual de 20 a 30 páginas reales con ejecuciones fuera de Git.
4. **Verdad de referencia manual:** La verdad de referencia debe anotarse manualmente mediante el esquema canónico `EstadilloDocument` y permanecer inmutable durante la ejecución. El benchmark nunca inventa ni auto-completa ground truth a partir de predicciones.

### Estructura de Formatos y Esquemas Versionados

- **`manifest.json` (`BenchmarkManifest`):** Manifiesto del dataset con versión de esquema, metadatos, configuración de pipelines a evaluar y lista de casos con rutas confinadas y flag `evaluate_structured` (para excluir croquis sin métricas tabulares).
- **`ground_truth/<caso>_gt.json` (`GroundTruthDocument`):** Documento canónico `EstadilloDocument` con la verdad de referencia anotada manualmente.
- **`predictions/<caso>_<pipeline>.json` (`BenchmarkPrediction`):** Documento canónico `EstadilloDocument` con la salida estructurada producida por un pipeline.
- **`report.json` / `report.md` (`BenchmarkReport`):** Informes agregado estructurado y resumen Markdown deterministas con conteos completos.

### Emparejamiento Global Determinista 1-a-1 sin Circularidad (Leave-One-Field-Out)

El algoritmo de matching (`src/fieldnotes/benchmark/matching.py`):
1. **Asignación global óptima (Húngaro / Kuhn-Munkres):** Resuelve el problema de asignación bipartita máxima en $O(N^3)$ garantizando una solución globalmente óptima frente a los fallos de emparejamiento subóptimo del greedy local.
2. **No circularidad estricta (Leave-One-Field-Out / LOFO):** Para evaluar la exactitud de cada campo individual (`col_exact`, `fil_exact`, `species_exact`, `height_exact`, `photo_exact`, `bbch_exact`), el propio campo objetivo se excluye completamente del cálculo de similitud y asignación. De este modo, una predicción nunca causa su propio emparejamiento.
3. **Evidencia multi-campo suficiente:** Exige identidad explícita por `id` exacto o concordancia en **al menos dos campos no vacíos evaluados**. Filas con una única coincidencia aislada (como un BBCH idéntico en filas completamente distintas) se rechazan (`score = 0.0`), impidiendo el autoemparejamiento artificial de registros espurios.
4. **Exclusión de croquis/sketch:** Casos catalogados con `evaluate_structured=false` (`case_05_sketch`) no se evalúan tabularmente ni afectan a los denominadores o métricas agregadas globales.
5. **Uno-a-uno estricto:** Cada fila de referencia se asigna a lo sumo a una fila de predicción y viceversa. Filas duplicadas en predicción se asignan una sola vez y las réplicas se penalizan como falsos positivos (`unmatched_predicted`).
6. **Manejo de anomalías y desempate estable:** Maneja de forma robusta filas reordenadas, faltantes, extras y duplicadas con ordenación jerárquica determinista (`score > num_matches > page_diff > idx_diff > ref_idx > pred_idx`).

### Las 8 Métricas Obligatorias y Semántica de Denominador Cero

Todas las métricas son puras, deterministas y reportan numerador, denominador y cociente en `[0.0, 1.0]` (nunca `NaN` ni `Infinity`). El esquema `MetricValue` valida estrictamente que `numerator <= denominator` y rechaza conteos imposibles sin recurrir a clamp que oculte invariantes rotas:

| Métrica | Descripción | Política de Cálculo |
|---|---|---|
| `row_precision` | Precisión de registros | `matched_rows / total_predicted_rows` (1.0 si ambos 0; 0.0 si pred > 0 y matched = 0) |
| `row_recall` | Recuperación de registros | `matched_rows / total_reference_rows` (1.0 si ambos 0; 0.0 si ref > 0 y matched = 0) |
| `col_exact` | Exactitud en columna | Aciertos de `col` en pares emparejados bajo LOFO `col` / `matched_lofo_col` |
| `fil_exact` | Exactitud en fila | Aciertos de `fil` en pares emparejados bajo LOFO `fil` / `matched_lofo_fil` |
| `species_exact` | Exactitud canónica en especie | Mapeo canónico (`Ap` $\rightarrow$ `P`, `Ah` $\rightarrow$ `H`, `Ar` $\rightarrow$ `R`, `Mz` $\rightarrow$ `M`) bajo LOFO `especie` |
| `height_exact` | Exactitud exacta en altura | Comparación numérica exacta en cm sin tolerancias ocultas bajo LOFO `altura_cm` |
| `photo_exact` | Exactitud en foto | Comparación textual exacta de referencia fotográfica bajo LOFO `foto` |
| **`bbch_exact`** | **Exactitud fenológica BBCH** | **Comparación canónica exacta del código BBCH bajo LOFO `bbch` (destacada en informes)** |

**Política de valores ausentes en pares emparejados:**
- Ausente / Ausente: Se considera coincidencia exacta (+1 acierto).
- Presente / Ausente o Ausente / Presente: Se considera discrepancia (+0 acierto).
- Presente / Presente: Comparación canónica estricta del valor.

### Ejecución del Benchmark por CLI y Determinismo por Defecto

Ejecución offline sobre el dataset fixture anonimizado:

```powershell
.\.venv\Scripts\python.exe -m src.fieldnotes.benchmark --manifest tests/fixtures/benchmark/manifest.json --output-dir ./benchmark_output
```

Opciones principales:
- `--manifest <ruta>`: Ruta al archivo `manifest.json`.
- `--dataset-dir <ruta>`: Directorio raíz para resolver rutas relativas confinadas.
- `--output-dir <ruta>` / `-o <ruta>`: Directorio de salida autorizado donde se guardan de forma atómica los archivos canónicos `report.json` y `report.md`.
- `--pipelines <p1> <p2>`: Filtrar pipelines específicos a evaluar (valida existencia en manifiesto).
- `--timestamp <ISO>`: Opcional. Por defecto, `generated_at` se deriva canónicamente del campo `created_at` del manifiesto, garantizando que dos ejecuciones CLI sucesivas generen artefactos byte-a-byte idénticos.

### Comparación Inicial: Unlimited-OCR solo vs. Unlimited-OCR + Qwen 3.5 9B

La evaluación comparativa sobre el dataset de prueba refleja las diferencias estructurales entre ambos enfoques:
1. **Unlimited-OCR directo (`ocr_only`):** Funciona como extractor base rápido, pero en tablas manuscritas o complejas presenta errores de desplazamiento de columnas, confusión en dígitos BBCH manuscritos (p. ej. `53` vs `58`) y especies sin normalizar (`Ap`, `Mz`).
2. **Unlimited-OCR + Qwen 3.5 9B (`ocr_plus_vlm`):** Al utilizar la imagen original como fuente primaria de evidencia y el OCR como hipótesis auxiliar estructurada mediante esquemas Pydantic, corrige desplazamientos de columnas, normaliza especies canónicamente y resuelve códigos fenológicos BBCH ambiguos con mayor exactitud.

## DiagramIR: Representación Intermedia Estructurada y Auditable (PR 9)

El módulo `src/fieldnotes/diagrams/` y el esquema `src/fieldnotes/schemas/diagram.py` introducen **DiagramIR**, una representación intermedia estructurada, auditable y tipada para digitalizar croquis de campo, diagramas de flujo y esquemas GPS sin generar código gráfico directo (SVG o Mermaid).

### Principios Arquitectónicos de DiagramIR

1. **Separación entre Estructura y Renderizado:** Los diagramas se representan primero como datos semánticos estructurados (`diagram.json`). El renderizado gráfico (Mermaid para flowcharts y SVG para croquis) corresponde exclusivamente a **PR 10**.
2. **Tipos Soportados:** `diagram_type` acepta exactamente:
   - `field_sketch`: Croquis de campo y parcelas experimentales.
   - `flowchart`: Diagramas de flujo y protocolos de decisión.
   - `gps_sketch`: Esquemas de ubicación de puntos y vértices GPS.
3. **Contrato de Prompt para VLM:** Se solicita a Qwen 3.5 9B structured JSON únicamente (`DiagramDTO`), prohibiendo explícitamente generar SVG/Mermaid, inferir relaciones o inventar coordenadas.

> [!WARNING]
> **Distinción Crítica: Coordenadas Visuales Frente a Coordenadas GPS**
> - **Coordenadas Visuales (`Point2D` / `BoundingBox2D`):** Valores normalizados flotantes estrictos en $[0.0, 1.0]$ donde $(0,0)$ es la esquina superior izquierda y $(1,1)$ la inferior derecha. Representan **ÚNICAMENTE la posición gráfica dentro del dibujo** y **JAMÁS deben interpretarse como coordenadas GPS ni geográficas**.
> - **Coordenadas Geográficas (`GeographicCoordinate`):** Modelo completamente separado que contiene `latitude` en $[-90.0, 90.0]$, `longitude` en $[-180.0, 180.0]$, elevación opcional, evidencia textual literal obligatoria (`raw_text`), página fuente obligatoria (`source_page >= 1`) y enlace opcional exclusivo a puntos visuales (`associated_point_id`).
> - **Invariante de Georreferenciación:**
>   - `georeferenced = False` $\implies$ `crs = None` y `geographic_coordinates = []`.
>   - `georeferenced = True` $\implies$ `crs` explícito obligatorio (ej: `'EPSG:4326'`, `'ETRS89 / UTM zone 30N'`; **nunca se asume EPSG:4326 por defecto**) y al menos una coordenada geográfica observada respaldada por `raw_text`.

### Entidades del Diagrama

- `PointEntity`: Puntos, vértices, hitos, nodos o árboles con posición visual relativa `coordinate: Point2D`.
- `LineEntity`: Líneas, límites, arroyos, transectos o flechas de conexión (`points: list[Point2D]`, `min_length=2`, no degeneradas; `source_point_id` y `target_point_id` resuelven exclusivamente a `PointEntity`).
- `AreaEntity`: Zonas, bancales, parcelas o pasos de proceso (`bbox: BoundingBox2D` o `polygon: list[Point2D]` no degenerado con área mayor que cero y vértices no colineales).
- `LabelEntity`: Anotaciones textuales legibles con posición visual opcional y referencia de anclaje `attached_to_id`.
- `RelationEntity`: Relaciones topológicas o de flujo (`source_id`, `target_id`, `relation_type`, `directed`).
- `DiagramOrientation`: Orientación declarada (`north_up`, `south_up`, `east_up`, `west_up`, `rotated`, `unknown`, `none`). Toda orientación declarada exige evidencia textual `raw_text`. `direction="rotated"` exige `degrees` numérico en $[0, 360]$; para el resto de orientaciones o si es `unknown`/`none`, `degrees` debe ser `None`.

Todas las entidades poseen identificadores únicos en todo el diagrama y las referencias internas (`source_id`, `target_id`, `source_point_id`, `target_point_id`, `attached_to_id`, `associated_point_id`) se validan de forma determinista contra entidades existentes del tipo permitido.

### Uso Programático de la API de Diagramas

```python
from src.fieldnotes.pipeline import UnlimitedOCRAgent
from src.fieldnotes.schemas.diagram import DiagramIR

# Instanciar agente con modelo de visión configurado
agent = UnlimitedOCRAgent(
    vision_model="qwen/qwen3.5-9b",
    output_dir="./output_ocr",
)

# Extraer y persistir DiagramIR desde una imagen (diagram_type es obligatorio)
diagram, artifacts = agent.process_diagram(
    image_path="croquis_parcela.png",
    diagram_type="field_sketch",
    output_dir="./output_ocr",
)

print(f"Tipo: {diagram.diagram_type}")
print(f"Áreas: {len(diagram.areas)}, Puntos: {len(diagram.points)}, Líneas: {len(diagram.lines)}")
print(f"Artefactos persistidos en: {artifacts['canonical_dir']}")
```

### Persistencia Canónica, Staging y Rollback

Al invocar `process_diagram` o `persist_diagram_artifacts`:
- Genera el layout canónico en `<output_dir>/<safe_stem>/`:
  - `diagram.json`: Serialización JSON estricta y determinista de `DiagramIR`.
  - `assets/<safe_stem>_original.<ext>`: Copia exacta byte a byte de la imagen original.
- **Cero renderizado gráfico:** No genera archivos `.svg`, `.mmd` ni `.mermaid`.
- **Staging y Rollback Atómico:** Toda escritura se realiza en un subdirectorio de staging temporal `.staging_diagram_<stem>_<uuid>`. En caso de sobreescritura, se crea un respaldo temporal `.backup_diagram_<stem>_<uuid>`. Si ocurre un fallo de validación o E/S, se restaura la versión previa y se eliminan todos los directorios temporales, garantizando confinamiento estricto y cero artefactos residuales.

### Alcance y Límites de PR 9

- **Incluido en PR 9:** Esquemas `DiagramIR` y DTOs, validación determinista, contrato de prompt VLM, extracción multimodal, persistencia JSON/assets con staging/rollback y suite de tests offline.
- **Completado en PR 10:** Renderizado de flowcharts a sintaxis Mermaid y croquis a SVG con descripción Markdown accesible.
- **Fuera de alcance (PR 11):** Integración de diagramas dentro del perfil general `notebook` y CLI global por defecto.

## Renderizado Determinista y Accesible de Diagramas (PR 10)

El módulo `src/fieldnotes/diagrams/` añade capacidades de renderizado **puro, determinista, seguro y accesible** para transformar instancias de `DiagramIR` en artefactos visuales y descripciones textuales sin volver a consultar al modelo VLM ni requerir GPU, red, navegador, Mermaid CLI ni Graphviz:

### Enrutamiento Estricto por Tipo de Diagrama

| Tipo de Diagrama (`diagram_type`) | Artefacto Visual Derivado | Motor de Renderizado | Salida Accesible |
|---|---|---|---|
| `flowchart` | `diagram.mmd` | `render_mermaid` (Mermaid puro) | Bloque ````mermaid ```` incrustado + texto |
| `field_sketch` | `assets/<stem>.svg` | `render_svg` (SVG XML estándar) | Enlace `![...](assets/...)` + texto |
| `gps_sketch` | `assets/<stem>.svg` | `render_svg` (SVG XML estándar) | Enlace `![...](assets/...)` + texto |

El enrutamiento es estricto: solicitar Mermaid para un croquis o SVG para un diagrama de flujo se rechaza de inmediato con `ValueError`.

### Seguridad Estricta y Neutralización de Inyecciones

1. **Mermaid (`src/fieldnotes/diagrams/render_mermaid.py`):**
   - **Identificadores internos seguros:** Los nodos se identifican exclusivamente mediante tokens deterministas generados por índice (`n0`, `n1`, `n2`, ...), completamente independientes del texto del documento o de IDs arbitrarios.
   - **Saneamiento de etiquetas (`sanitize_mermaid_label`):** Neutraliza saltos de línea (`\r\n`, `\n`), comillas dobles, comillas invertidas, barras invertidas, punto y coma, delimitadores de Mermaid (`[ ]`, `{ }`, `( )`, `|`), entidades HTML (`<`, `>`, `&`) y palabras clave estructurales (`subgraph`), impidiendo la inyección de nodos espurios, aristas no autorizadas o scripts.
2. **SVG XML (`src/fieldnotes/diagrams/render_svg.py`):**
   - **XML estándar válido:** Validado automáticamente con `xml.etree.ElementTree`.
   - **Lienzo fijo y escalado lineal:** Utiliza un `viewBox="0 0 1000 1000"` fijo donde las coordenadas relativas $[0.0, 1.0]$ se escalan linealmente a $[0.0, 1000.0]$. Las coordenadas visuales **JAMÁS** se transforman en coordenadas GPS.
   - **Prohibición absoluta de contenido activo:** El renderizador nunca emite elementos `<script>`, `<foreignObject>`, enlaces (`<a>`, `href`, `xlink:href`), imágenes externas (`<image>`) ni referencias remotas por URL.
   - **Estilos estáticos:** Emplea paletas visuales sobrias y constantes definidas en código, diferenciando puntos de muestreo (rojo), vegetación/árboles (verde), vértices (púrpura) y elementos genéricos (naranja).
   - **Indicadores de orientación fieles:** Si el diagrama tiene una orientación explícita declarada (`north_up`, `south_up`, `east_up`, `west_up`, `rotated` con grados), dibuja un indicador visual en la esquina superior derecha. Si la orientación es `unknown` o `none`, **no dibuja ninguna rosa de los vientos engañosa**.

### Descripción Markdown Accesible y No Inferencial (`render_markdown`)

Genera un documento Markdown autosuficiente estructurado para lectura humana y análisis por LLMs sin visión:
- **Inclusión del recurso:** Incrusta el bloque Mermaid o enlaza al archivo SVG relativo.
- **Declaración explícita sobre GPS:**
  - Cuando `georeferenced = False`: Declara explícitamente: `"- **Georreferenciación:** No georreferenciado. El croquis no contiene coordenadas GPS exactas (las posiciones visuales observadas son relativas al dibujo [0.0, 1.0])."`
  - Cuando `georeferenced = True`: Lista el CRS explícito y cada coordenada geográfica observada con su `raw_text`, latitud, longitud, elevación y procedencia.
- **Orientación:** Describe la orientación únicamente cuando existe evidencia textual observada.
- **Desglose de entidades:** Detalla áreas (con BBox o polígonos), puntos (con coordenadas relativas $x, y$), líneas (conectividad, dirección), etiquetas legibles y relaciones explícitas.
- **Invariante de no inferencia:** El generador **NUNCA inventa** relaciones cardinales (p. ej. "al oeste de") ni topologías espaciales no especificadas en los campos de `DiagramIR`.

### Publicación Canónica Atómica y Rollback (`publish_rendered_diagram`)

La publicación consolida los artefactos en el directorio canónico `<output_dir>/<stem>/`:
- `diagram.json`: Serialización JSON exacta de `DiagramIR` (evidencia canónica).
- `assets/<stem>_original.<ext>`: Copia byte a byte inalterada de la imagen original.
- `diagram.md`: Descripción Markdown accesible.
- `diagram.mmd` (si es `flowchart`) o `assets/<stem>.svg` (si es `field_sketch` / `gps_sketch`).

**Garantías:**
- **Confinamiento estricto:** Rechaza intentos de Directory Traversal en `document_name` o rutas de salida.
- **Staging y Rollback:** Las escrituras ocurren en un directorio temporal aislado. Ante cualquier fallo de E/S o validación, se limpia el staging y se restaura el directorio previo desde el backup, evitando estados parciales o corruptos.

### Uso Programático del Renderizado

```python
from src.fieldnotes.schemas.diagram import DiagramIR
from src.fieldnotes.diagrams.rendering import render_diagram, publish_rendered_diagram

# 1. Renderizado puro en memoria
result = render_diagram(diagram)
print(f"Tipo: {result.diagram_type}")
if result.mermaid_code:
    print("Código Mermaid generado:")
    print(result.mermaid_code)
elif result.svg_content:
    print("SVG generado (longitud):", len(result.svg_content))

# 2. Publicación atómica completa
published_files = publish_rendered_diagram(
    diagram=diagram,
    source_image_path="croquis.png",
    output_base_dir="./output_ocr",
    document_name="croquis_parcela_01",
)
print("Archivos publicados:", published_files)
```
