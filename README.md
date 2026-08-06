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
