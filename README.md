# Agente de OCR con Unlimited-OCR y LM Studio

Herramienta local para digitalizar documentos PDF e imágenes mediante el modelo multimodal `baidu/Unlimited-OCR`. Puede exportar el resultado a Markdown o PDF y, opcionalmente, enviarlo a LM Studio para análisis adicional.

Unlimited-OCR debe entenderse como un modelo de OCR y parsing visual de documentos. Su objetivo es reconocer texto, tablas, fórmulas y estructura en imágenes, incluso a lo largo de varias páginas. No es un modelo de resumen ni un compresor de contexto para sustituir a un LLM.

## Requisitos

- Python 3.10 a 3.12
- `uv`
- LM Studio, únicamente para consultas y procesamiento posterior al OCR
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
  - `pipeline.py`: orquestador `UnlimitedOCRAgent` con soporte de `ocr_mode` (`worker` e `in_process`).
  - `config.py`: configuración de entorno, timeout del worker y codificación.
  - `export.py`: exportación a Markdown y PDF.
  - `cli.py`: interfaz de línea de comandos.
- `setup_env.py`: detección de hardware e instalación de PyTorch.
- `requirements.txt`: dependencias Python.
- `26-05-06.pdf`: documento de prueba incluido en el repositorio.

## Arquitectura de ejecución y liberación de VRAM

En sistemas con 12 GB de VRAM (NVIDIA RTX 4070 Ti), `Unlimited-OCR` y `Qwen 3.5 9B` (vía LM Studio) se ejecutan de forma estrictamente secuencial:
1. **Rasterizado**: El proceso padre convierte el PDF a imágenes en `pages/page_001.png...`.
2. **Inferencia OCR aislada**: Por defecto (`ocr_mode="worker"`), se lanza un subproceso hijo independiente mediante `subprocess` (`shell=False`).
3. **Liberación de VRAM**: Al completar la extracción, el proceso worker finaliza y el sistema operativo reclama la memoria CUDA. El proceso padre nunca importa `torch` ni `transformers` en este modo.
4. **Procesamiento VLM**: El proceso padre lee el texto extraído desde `result.md` e interactúa con LM Studio con la GPU disponible.

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

## Configuración de LM Studio

El agente consulta `/v1/models` y selecciona automáticamente el primer modelo de texto disponible. También puedes fijar el modelo explícitamente:

```powershell
$env:LM_STUDIO_MODEL="qwen/qwen3.6-27b"
python agent.py documento.pdf "¿Cuántas filas existen en el experimento?" --export-md respuesta.md
```

O mediante la opción equivalente:

```powershell
python agent.py documento.pdf "¿Cuántas filas existen en el experimento?" --lm-model qwen/qwen3.6-27b --export-md respuesta.md
```

No se debe usar `local-model` salvo que ese sea realmente el identificador anunciado por `/v1/models`.

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

Ejecución de la suite completa de tests unitarios offline:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Para ejecutar opcionalmente la prueba de integración con el worker OCR real y liberación de VRAM (requiere GPU con CUDA y el PDF de prueba):

```powershell
$env:RUN_OCR_WORKER_INTEGRATION="1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
