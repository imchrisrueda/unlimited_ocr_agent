# Arquitectura

Estado: activa.

## Contexto del sistema

Aplicación CLI local que transforma PDF o imágenes de notas de campo en artefactos estructurados y auditables. Unlimited-OCR aporta una hipótesis textual; un Qwen multimodal servido por LM Studio interpreta la página bajo esquemas Pydantic; Python fusiona, valida y renderiza los resultados.

## Components and boundaries

- **CLI y pipeline** (`agent.py`, `src/fieldnotes/cli.py`, `pipeline.py`): selección del perfil y orquestación.
- **Ingesta** (`ingest/`): renderizado de páginas.
- **OCR** (`ocr/`): extracción aislada, preferentemente mediante worker.
- **VLM** (`vlm/`): cliente OpenAI-compatible de LM Studio y respuestas estructuradas.
- **Esquemas, fusión y validación** (`schemas/`, `merge/`, `normalization/`, `validation/`): modelo intermedio y reglas deterministas.
- **Renderizado** (`render/`): serialización sin inferencias adicionales.
- **Revisión** (`review/`): incidencias y recortes vinculados a evidencia.
- **Benchmark** (`benchmark/`): comparación reproducible de predicciones.

## Flujo de datos

1. La ingesta convierte el documento en imágenes por página.
2. El worker de OCR produce texto bruto y termina, liberando su memoria.
3. Qwen recibe la imagen como evidencia primaria y el OCR como apoyo.
4. La respuesta estructurada se valida y fusiona en orden documental.
5. Las reglas detectan conflictos, valores inciertos y discontinuidades.
6. Un renderer determinista publica los artefactos de manera atómica.
7. El operador revisa `review/issues.json` y las evidencias asociadas.

## Perfiles y almacenamiento

| Perfil | Uso | Salida principal |
|---|---|---|
| `default` | OCR, consulta o exportación general | respuesta, Markdown o PDF solicitado |
| `estadillo` | jornadas tabulares de campo | `<fecha>/notas.md` y `datos.csv` |
| `notebook` | cuadernos heterogéneos | `<stem>/notebook.md` |

El perfil estadillo publica además `document.json`, `pages/`, `raw/`, `assets/` y `review/`. Si la fecha no es inequívoca, usa un nombre seguro derivado del archivo y crea una incidencia; nunca inventa la fecha.

## Interfaces

- CLI estable: `python agent.py ...`.
- API Python: `UnlimitedOCRAgent`.
- HTTP local OpenAI-compatible de LM Studio.
- Sistema de archivos como interfaz de entrega y auditoría.

## Testing architecture

La suite unitaria no requiere red, GPU ni LM Studio. La integración real se activa explícitamente con `RUN_ESTADILLO_INTEGRATION=1`. Los gates mínimos son unittest, compileall, `projectctl validate` y `git diff --check`.

## Seguridad y privacidad

La aplicación no necesita servicios cloud. Los documentos permanecen locales si LM Studio y los modelos están instalados localmente. Las credenciales y modelos no se versionan.

## Decisiones y compromisos

La ejecución secuencial OCR→VLM tarda más, pero reduce la competencia por VRAM. El JSON intermedio y el renderizado determinista añaden complejidad, pero conservan procedencia y evitan que el modelo escriba directamente el producto final. Las relaciones entre fechas son una capa interpretativa: deben citar archivos y separar hechos de inferencias.
