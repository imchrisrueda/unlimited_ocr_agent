# Manual de instalación y uso

## 1. Requisitos

- Windows PowerShell.
- Python 3.11 administrado por `uv`.
- GPU NVIDIA compatible con el OCR.
- LM Studio iniciado con un modelo multimodal, por ejemplo `qwen/qwen3.5-9b`.

## 2. Instalación reproducible

```powershell
git clone https://github.com/imchrisrueda/unlimited_ocr_agent.git
cd unlimited_ocr_agent
git switch codex/pr1-safe-refactor
uv venv --python 3.11
.\.venv\Scripts\python.exe setup_env.py
```

Comprueba el entorno:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
Invoke-RestMethod "http://localhost:1234/v1/models"
nvidia-smi
```

## 3. Modos de uso

| Necesidad | Perfil | Entrega principal |
| --- | --- | --- |
| Estadillo agronómico o forestal | `estadillo` | `notas.md`, `datos.csv` y `review/datos.xlsx` |
| Cuaderno visual y secuencial | `cuaderno_campo` | `cuaderno_campo.md` |
| Documento mixto heredado | `notebook` | `notebook.md` |
| OCR sin VLM | `--raw` | Texto OCR |

Usa `estadillo` solo cuando la fuente sea realmente un estadillo. Un documento sin registros produce un CSV vacío; esto es correcto y evita inventar filas.

## 4. Perfil estadillo

```powershell
.\.venv\Scripts\python.exe agent.py documento.pdf --profile estadillo --vision-model $env:LM_STUDIO_VISION_MODEL --output output_ocr
```

Entrega:

```text
output_ocr/<sesión>/
├── notas.md
├── datos.csv
├── document.json
├── pages/
├── raw/
├── assets/
└── review/
    ├── datos.xlsx
    └── issues.json```

La fecha ISO se usa como carpeta únicamente si aparece de forma inequívoca. La cabecera CSV es:

```text
id,col,fil,especie,altura_cm,foto,bbch,observaciones
```

No se completan datos ausentes. Revisa siempre `review/issues.json` y la página fuente.

## 5. Perfil cuaderno_campo

```powershell
.\.venv\Scripts\python.exe agent.py documento.pdf --profile cuaderno_campo --vision-model $env:LM_STUDIO_VISION_MODEL --output output_ocr
```

Entrega:

```text
output_ocr/<documento>/
├── cuaderno_campo.md
├── document.json
├── pages/
├── raw/
├── assets/
└── review/issues.json
```

Reglas del resultado:

1. Conserva las páginas y el orden secuencial.
2. No muestra las tablas canónicas de estadillo.
3. Extrae texto y figuras mediante pasadas VLM separadas.
4. Digitaliza flujos como Mermaid y croquis como SVG.
5. Inserta después la imagen original de la página.
6. Elimina referencias geométricas rotas con una advertencia; no inventa entidades.
7. Evita repetir contenido idéntico devuelto como resumen y párrafo.

El Markdown enlaza recursos relativos. Mantén juntos el archivo, `pages/` y `assets/`.

## 6. Revisión de cuaderno_campo

Comprueba:

1. Que todas las páginas aparecen como `## Página N`.
2. Que cada página enlaza `pages/page_NNN.png`.
3. Que los flujos contienen un bloque `mermaid` con nodos y conexiones.
4. Que los croquis enlazan un SVG de `assets/`.
5. Que no aparece la cabecera `id,col,fil,especie,...`.
6. Que cualquier descarte figure como advertencia y pueda contrastarse con el original.

## 7. Supervisión de recursos

El modo OCR predeterminado es `worker`: ejecuta OCR en un proceso aislado y libera sus recursos antes del VLM. Ejecuta un solo documento a la vez para evitar competencia por VRAM.

```powershell
$logDir = "C:\tmp\ocr-monitor"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$run = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "agent.py","documento.pdf","--profile","cuaderno_campo","--vision-model",$env:LM_STUDIO_VISION_MODEL,"--output","output_ocr" -PassThru -WindowStyle Hidden -RedirectStandardOutput "$logDir\stdout.log" -RedirectStandardError "$logDir\stderr.log"
while (Get-Process -Id $run.Id -ErrorAction SilentlyContinue) {
  Get-Process -Id $run.Id | Select-Object Id,CPU,WorkingSet64
  nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader
  Start-Sleep -Seconds 15
}
```

No uses `$PID` como variable: PowerShell la reserva. LM Studio puede permanecer activo como servicio; no deben quedar procesos Python del proyecto después de finalizar.

## 8. Validación real realizada

Se validaron ambos perfiles con `26-05-06.pdf`, `notas_campo.pdf` y `diagrama.pdf`:

> Los PDF aportados para esta validación no se publican en el repositorio. Usa documentos propios equivalentes para repetir las pruebas reales.

- `cuaderno_campo`: 3/3 ejecuciones correctas; Mermaid, SVG e imágenes originales comprobados.
- `estadillo`: 3/3 ejecuciones correctas; los dos documentos sin estadillo produjeron cero filas y `26-05-06.pdf` produjo 130 registros.
- Matriz total: 6/6 ejecuciones con código 0.

## 9. Problemas frecuentes

| Problema | Acción |
| --- | --- |
| LM Studio no responde | Comprueba el servidor y `/v1/models`. |
| Falta VRAM | Usa `worker` y evita ejecuciones paralelas. |
| Mermaid sin conexiones | Revisa la advertencia y la imagen original; el perfil reintenta la extracción semántica. |
| Geometría inválida | Se descarta la referencia, nunca se inventa. |
| Salida incierta | Contrasta `review/issues.json` con `pages/`. |

No versiones `.venv/`, modelos, cachés, credenciales, logs ni salidas temporales.