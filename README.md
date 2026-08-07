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

La opción `--raw` devuelve directamente el Markdown generado por Unlimited-OCR, sin consultar LM Studio. Para una imagen se utiliza `infer` con `save_results=True`. Para un PDF, el agente rasteriza las páginas a 300 DPI y utiliza `infer_multi`, que es el flujo recomendado por el proyecto oficial para parsing multipágina. Se activan los parámetros de prevención de repetición recomendados por el modelo (`no_repeat_ngram_size=35` y `ngram_window=1024` en documentos multipágina).

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

- `agent.py`: procesamiento OCR, integración con LM Studio y exportación.
- `setup_env.py`: detección de hardware e instalación de PyTorch.
- `requirements.txt`: dependencias Python.
- `26-05-06.pdf`: documento de prueba incluido en el repositorio.

## Uso adecuado y límites

- Para imágenes individuales: `infer` con `<image>document parsing.` y `save_results=True`.
- Para PDF o documentos multipágina: convertir las páginas a imágenes y usar `infer_multi` con `<image>Multi page parsing.`.
- El resultado OCR puede contener HTML de tablas, coordenadas, etiquetas de detección o errores de reconocimiento. Debe validarse antes de usarlo como dato estructurado.
- La conversión a PDF de este proyecto es una exportación visual del texto Markdown; no reconstruye automáticamente un PDF editable con el diseño original.

La implementación se basa en la [documentación oficial de Unlimited-OCR](https://github.com/baidu/Unlimited-OCR), que distingue explícitamente entre `infer` para una imagen y `infer_multi` para varias páginas.
## Archivos intermedios

Durante la inferencia se generan imágenes de las páginas, resultados raw y, en ocasiones, imágenes con cajas de detección dentro de un subdirectorio temporal de `output_ocr`. Son útiles para depurar el OCR, revisar el reconocimiento visual o conservar evidencias de una ejecución, pero no son necesarios después de exportar el Markdown o el PDF.

Por defecto, el agente elimina ese subdirectorio al finalizar. Para conservarlo durante una ejecución concreta:

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

Para consultas posteriores a OCR, el agente envía `reasoning_effort="none"` por defecto. Esto evita que modelos como Gemma consuman todo el límite de salida en `reasoning_content` y devuelvan `content` vacío. Si LM Studio devuelve una respuesta sin contenido, el terminal muestra una ayuda indicando esta causa y las alternativas: usar un modelo no razonador o aumentar el límite de tokens.
