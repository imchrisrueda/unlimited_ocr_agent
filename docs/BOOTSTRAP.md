# Instalación y réplica

## Requisitos

- Git.
- Python 3.11.
- `uv`.
- GPU NVIDIA y un PyTorch/CUDA compatible para ejecutar Unlimited-OCR.
- LM Studio con un Qwen multimodal disponible para los perfiles visuales.

## Preparación

```powershell
git clone <URL_DEL_REPOSITORIO>
cd unlimited_ocr_agent
uv venv --python 3.11
.\.venv\Scripts\python.exe setup_env.py
```

En Linux usa `.venv/bin/python`.

## Qwen local

Inicia el servidor de LM Studio y carga un Qwen capaz de visión. Después define:

```powershell
$env:LM_STUDIO_URL = "http://localhost:1234"
$env:LM_STUDIO_API_KEY = "lm-studio"
$env:LM_STUDIO_VISION_MODEL = "identificador-del-modelo"
$env:LM_STUDIO_TEXT_MODEL = "identificador-del-modelo"
```

El identificador debe coincidir con el que publica LM Studio. No se descarga ni inicia ningún modelo automáticamente.

## Verificación portable

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe scripts\projectctl.py validate
```

## Verificación funcional

Con LM Studio y Qwen activos:

```powershell
.\.venv\Scripts\python.exe agent.py 26-05-06.pdf --profile estadillo --vision-model "identificador-del-modelo" --output output_ocr
```

La entrega esperada es `output_ocr/2026-05-06/notas.md` más `datos.csv`, junto con evidencia y revisión. Compara la estructura con `gt/`, sin copiar sus valores.

No se deben versionar `.venv/`, modelos, cachés, logs, salidas OCR ni credenciales.
