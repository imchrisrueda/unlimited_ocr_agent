# Contexto y especificación de mejora: `unlimited_ocr_agent`

Repositorio:

`imchrisrueda/unlimited_ocr_agent`

## Objetivo del proyecto

Este proyecto digitaliza notas de campo principalmente manuscritas.

Las fuentes son sobre todo PDFs escaneados o generados a partir de fotografías y pueden contener:

* texto manuscrito;
* tablas manuscritas;
* registros fenológicos BBCH;
* notas experimentales;
* listas y anotaciones;
* croquis de parcelas;
* esquemas de ubicación aproximada de puntos GPS;
* diagramas de flujo sencillos.

El producto principal debe ser un **cuaderno de campo en Markdown**, diseñado tanto para lectura humana como para análisis posterior mediante otro LLM.

La precisión y la trazabilidad son más importantes que producir una salida visualmente perfecta.

No se deben inventar datos ausentes o ilegibles.

---

# Hardware y ejecución

Hardware principal:

* NVIDIA RTX 4070 Ti
* 12 GB VRAM

Todo el procesamiento debe poder ejecutarse localmente.

LM Studio se ejecuta localmente y expone una API compatible con OpenAI.

Modelo VLM seleccionado para este proyecto:

`Qwen 3.5 9B`

Qwen 3.5 9B debe ser el único VLM utilizado inicialmente.

No introducir otros modelos en el pipeline salvo que quede detrás de una interfaz configurable y no sea necesario para el funcionamiento normal.

---

# Estado actual del proyecto

El proyecto utiliza:

* `baidu/Unlimited-OCR`
* PyMuPDF
* PyTorch
* Transformers
* LM Studio mediante el cliente `openai`

Actualmente `agent.py` concentra la mayor parte de la lógica.

El flujo actual es aproximadamente:

```text
PDF
 ↓
rasterizado a imágenes
 ↓
Unlimited-OCR
 ↓
Markdown OCR
 ↓
LM Studio recibe únicamente el texto OCR
 ↓
Markdown final
```

Unlimited-OCR funciona razonablemente bien como extractor de texto y tablas, pero existen errores importantes en documentos manuscritos:

* columnas desplazadas;
* tablas con distinto número de columnas entre páginas;
* confusión entre altura, fotografía y BBCH;
* reconocimiento erróneo de códigos;
* errores en abreviaturas de especies;
* contenido espurio;
* dificultad especial con croquis y gráficos.

El mayor problema arquitectónico actual es que LM Studio sólo recibe el texto generado por Unlimited-OCR.

Cuando Unlimited-OCR no interpreta correctamente un croquis o una celda, el segundo modelo ya no puede consultar la imagen original.

Esto debe cambiar.

---

# Principio arquitectónico principal

La imagen original debe considerarse la fuente primaria de evidencia.

El OCR debe tratarse como una hipótesis o ayuda para el VLM, no como la fuente definitiva.

El flujo objetivo es:

```text
PDF
 ↓
páginas como imágenes
 ↓
Unlimited-OCR
 ↓
OCR raw por página
 ↓
Qwen 3.5 9B recibe:
    - imagen original o crop
    - OCR correspondiente
    - instrucción específica
 ↓
JSON estructurado
 ↓
Pydantic / validación Python
 ↓
DocumentIR
 ↓
renderer determinista
 ↓
Markdown
```

El VLM no debe generar directamente el Markdown definitivo siempre que pueda evitarse.

---

# Restricción importante de VRAM

Unlimited-OCR y Qwen 3.5 9B no deben competir simultáneamente por los 12 GB de VRAM.

El procesamiento debe realizarse por fases.

Preferencia:

```text
1. rasterizar
2. ejecutar Unlimited-OCR
3. guardar resultados
4. terminar/unload del proceso OCR
5. liberar GPU completamente
6. ejecutar Qwen 3.5 9B mediante LM Studio
7. estructurar y renderizar
```

La opción preferida es ejecutar Unlimited-OCR en un worker/proceso separado.

Al terminar ese proceso, CUDA debe liberar completamente su memoria.

Evitar depender únicamente de `torch.cuda.empty_cache()` si un proceso separado proporciona una liberación más fiable.

---

# Primera tarea: refactor seguro

Antes de añadir funcionalidad nueva, refactorizar `agent.py`.

No romper la CLI existente durante este primer paso.

Propuesta aproximada:

```text
src/
└── fieldnotes/
    ├── cli.py
    ├── pipeline.py
    ├── config.py
    │
    ├── ingest/
    │   └── pdf.py
    │
    ├── ocr/
    │   ├── unlimited.py
    │   └── worker.py
    │
    ├── vlm/
    │   └── lmstudio.py
    │
    ├── schemas/
    │   ├── document.py
    │   ├── estadillo.py
    │   └── diagrams.py
    │
    ├── profiles/
    │   ├── estadillo.py
    │   └── notebook.py
    │
    ├── validation/
    │   └── estadillo.py
    │
    └── render/
        └── markdown.py
```

No es obligatorio seguir literalmente estos nombres si existe una organización mejor, pero debe mantenerse separación clara de responsabilidades.

---

# Artefactos por página

La unidad básica de procesamiento debe pasar de ser "documento como gran string" a "página".

Crear una estructura similar a:

```python
class PageArtifact(BaseModel):
    page_number: int
    image_path: Path
    raw_ocr: str
```

El pipeline debe conservar siempre:

```text
page_001.png
page_001_ocr.md

page_002.png
page_002_ocr.md
...
```

Puede mantenerse `infer_multi` si sigue siendo útil para Unlimited-OCR, pero el resultado debe poder relacionarse posteriormente con páginas concretas.

Si resulta más fiable ejecutar OCR por página para mantener esa relación, evaluarlo mediante tests antes de cambiar el comportamiento.

---

# Eliminar chunking arbitrario por caracteres

El código actual divide documentos largos utilizando número de caracteres.

No utilizar este método para datos estructurados.

Nunca cortar:

* filas de tablas;
* observaciones asociadas a registros;
* páginas;
* diagramas.

Las unidades de procesamiento deben ser:

* página;
* tabla;
* bloque;
* registro.

Para documentos grandes, analizar página por página y fusionar posteriormente mediante Python.

---

# Cliente de LM Studio

Crear una capa explícita para LM Studio.

Debe existir como mínimo:

```python
ask_text(...)
```

y:

```python
ask_vision(...)
```

Conceptualmente:

```python
ask_vision(
    model,
    image_path,
    prompt,
    context=None,
    schema=None,
)
```

Qwen 3.5 9B debe recibir una imagen de página o un crop y opcionalmente el OCR correspondiente.

El modelo debe ser configurable, pero el default recomendado para visión será Qwen 3.5 9B.

No seleccionar automáticamente el primer modelo disponible en `/v1/models`.

Usar una configuración explícita similar a:

```text
LM_STUDIO_VISION_MODEL=<id-qwen-3.5-9b>
```

Opcionalmente:

```text
LM_STUDIO_TEXT_MODEL=<id-modelo-texto>
```

Pero inicialmente Qwen 3.5 9B puede realizar también tareas de transformación si simplifica el sistema.

---

# Structured outputs

Utilizar JSON Schema siempre que la tarea tenga estructura conocida.

Preferiblemente definir esquemas mediante Pydantic.

El VLM no debe devolver Markdown para tareas como estadillos.

Ejemplo:

```python
class EstadilloRow(BaseModel):
    id: str | None = None
    col: int | None = None
    fil: int | None = None
    especie_raw: str | None = None
    especie: str | None = None
    altura_cm: float | None = None
    foto: str | None = None
    bbch: str | None = None
    observaciones: str | None = None

    source_page: int

    uncertain: bool = False
    alternatives: list[str] = []
```

La definición exacta puede mejorarse.

La prioridad es:

* no perder la fuente;
* poder indicar incertidumbre;
* mantener página de procedencia;
* separar valor leído de valor normalizado.

---

# Evidencia y normalización

Cuando un dato se normalice, mantener si es posible:

```text
raw
normalized
source_page
uncertain
alternatives
```

Ejemplo:

```json
{
  "raw": "M2",
  "normalized": "M",
  "source_page": 3,
  "uncertain": true,
  "alternatives": ["Mz", "M2"]
}
```

La salida Markdown puede mostrar sólo el valor normalizado.

El JSON interno debe conservar la evidencia.

No corregir silenciosamente datos ambiguos.

---

# DocumentIR

Introducir una representación intermedia independiente del Markdown.

Ejemplo conceptual:

```python
class DocumentIR(BaseModel):
    source_file: str
    pages: list[PageIR]
    warnings: list[WarningIR] = []
```

Y bloques:

```python
BlockType = Literal[
    "text",
    "table",
    "field_sketch",
    "flowchart",
    "gps_sketch",
    "unknown",
]
```

No es necesario implementar todos los tipos desde el primer PR.

La primera implementación práctica debe centrarse en `estadillo`.

---

# Perfil `estadillo`

Este será el primer caso de uso estructurado completo.

Un estadillo contiene registros de campo distribuidos frecuentemente en varias páginas.

La salida final deseada debe ser **una única tabla Markdown**.

La tabla puede seguir el esquema actual del proyecto:

```md
|id|col|fil|especie|altura_cm|foto|bbch|observaciones|
|---|---|---|---|---|---|---|---|
```

El Markdown final debe construirse mediante Python.

El LLM/VLM no debe ser responsable de producir la sintaxis final.

Pipeline:

```text
páginas
 ↓
Unlimited-OCR
 ↓
OCR por página
 ↓
Qwen 3.5 9B:
    imagen + OCR
 ↓
EstadilloPage JSON
 ↓
merge Python
 ↓
validación
 ↓
EstadilloDocument
 ↓
renderer Python
 ↓
una tabla Markdown
```

---

# Prompt de visión para estadillos

El prompt debe ser específico y poco creativo.

Idea base:

```text
Analiza únicamente la tabla visible en esta imagen.

La imagen es la fuente de verdad.
El OCR suministrado es sólo una pista y puede contener errores.

Extrae cada registro como una fila independiente.

No inventes valores.

Si un valor no es legible:
- usa null si no puede determinarse;
- marca uncertain=true;
- incluye alternativas sólo cuando sean visualmente plausibles.

Respeta las columnas semánticas:
id, col, fil, especie, altura_cm, foto, bbch, observaciones.

No produzcas Markdown.
Devuelve únicamente la estructura JSON solicitada.
```

Ajustar el prompt para aprovechar JSON Schema.

---

# Validadores para estadillos

Añadir validadores deterministas.

No corregir automáticamente si no existe evidencia suficiente.

Detectar como mínimo:

## Duplicados

Dos registros con el mismo `(col, fil)` cuando esa combinación debería ser única.

## Secuencias sospechosas

Ejemplo esperado:

```text
26,25,24,23,22,21,20...
```

Si aparece:

```text
26,25,24,23,22,2,20...
```

marcar el valor sospechoso.

No convertir automáticamente `2` en `21` salvo que exista una política explícita y auditable.

## BBCH

Comprobar formato y rango plausible.

Valores claramente incompatibles deben generar warning.

No transformar silenciosamente `245` en `24`.

## Especies

Conservar siempre `especie_raw`.

Las normalizaciones existentes:

```text
Ap → P
Ah → H
Ar → R
Mz → M
```

pueden aplicarse en una capa separada.

Nunca perder el valor original leído.

## Filas ausentes

Detectar discontinuidades cuando la estructura del documento sugiera claramente una secuencia.

Sólo generar warning.

---

# Sistema de review

Crear un mecanismo de revisión explícita.

Ejemplo:

```python
class ReviewIssue(BaseModel):
    page: int
    field: str
    row_key: str | None
    reason: str
    candidates: list[str] = []
    crop_path: str | None = None
```

Guardar issues en:

```text
review/issues.json
```

Cuando sea posible, crear crop de la región problemática.

Ejemplo:

```text
review/
    p003_col2_fil17_especie.png
```

Objetivo:

```text
Procesamiento completado.
Registros: 156
Elementos que requieren revisión: 4
```

Es preferible marcar cuatro dudas que introducir cuatro errores silenciosos.

---

# Crops para celdas difíciles

Diseñar el sistema para permitir una segunda pasada sobre regiones concretas.

Ejemplo:

```text
Unlimited-OCR: altura = 105
Qwen página completa: altura = 10.5
 ↓
crear crop de esa celda
 ↓
Qwen 3.5 9B analiza únicamente el crop
```

No es necesario implementar esta lógica en el primer PR, pero la arquitectura debe permitirla.

---

# Salidas del pipeline

No producir únicamente `resultado.md`.

La estructura deseable por documento es:

```text
output/
└── 26-05-06/
    ├── notebook.md
    ├── document.json
    │
    ├── pages/
    │   ├── page_001.png
    │   └── page_002.png
    │
    ├── raw/
    │   ├── page_001.md
    │   └── page_002.md
    │
    ├── assets/
    │
    └── review/
        └── issues.json
```

Para el perfil `estadillo`, el archivo Markdown final debe contener únicamente la estructura definida para ese perfil.

El JSON mantiene trazabilidad y metadata.

---

# Croquis — segunda fase

No implementar croquis antes de tener funcional el pipeline estructurado para estadillos.

Una vez completado:

```text
imagen
 ↓
Qwen 3.5 9B
 ↓
DiagramIR
```

Tipos iniciales:

```text
field_sketch
flowchart
gps_sketch
```

---

# DiagramIR

No pedir al VLM generar SVG directamente.

Primero generar una descripción estructurada.

Ejemplo conceptual para un croquis:

```json
{
  "type": "field_sketch",
  "georeferenced": false,
  "orientation": "north-up",
  "areas": [],
  "points": [],
  "lines": [],
  "labels": [],
  "relations": []
}
```

Las posiciones dibujadas deben poder expresarse con coordenadas relativas entre 0 y 1.

Ejemplo:

```json
{
  "id": "P1",
  "x": 0.23,
  "y": 0.51
}
```

Estas coordenadas representan posición en el dibujo, no coordenadas GPS.

---

# GPS

Nunca inferir coordenadas geográficas a partir de la posición visual de un punto.

Diferenciar explícitamente:

```json
"georeferenced": false
```

de:

```json
{
  "georeferenced": true,
  "crs": "EPSG:4326"
}
```

Sólo marcar como georreferenciado cuando existan coordenadas reales legibles en el documento.

---

# Renderizado de diagramas

Una vez obtenido `DiagramIR`:

* diagramas de flujo → Mermaid;
* croquis de campo → SVG generado mediante código;
* conservar además la imagen original.

Ejemplo:

```text
assets/
    sketch_001_original.png
    sketch_001.svg
```

El Markdown debe incluir también una descripción textual del croquis para que otro LLM pueda entenderlo sin necesidad de visión.

Ejemplo:

```md
### Croquis del experimento

![Croquis](assets/sketch_001.svg)

- El bloque A se encuentra al oeste del bloque B.
- El punto P1 aparece junto al límite norte.
- El croquis no contiene coordenadas GPS exactas.
```

---

# Perfil de cuaderno general

Una vez implementados estadillos y croquis, crear un perfil de salida general.

Podrá combinar:

```text
# Fecha / sesión

## Objetivo

## Condiciones

## Notas
- ...
- ...

## Observaciones

## Mediciones

|...|

## Croquis

![...](...)

Descripción textual...
```

No asumir que todos los documentos tienen exactamente esta estructura.

El perfil debe poder adaptarse mediante schemas/configuración.

---

# Configuración por perfiles

Sustituir gradualmente el `AGENTS.md` monolítico por perfiles explícitos.

Ejemplo:

```text
profiles/
    estadillo.yaml
    notebook.yaml
    experiment.yaml
```

Un perfil puede definir:

```yaml
name: estadillo

vision:
  verify_every_page: true

output:
  type: single_table

validation:
  bbch: true
  row_sequence: true
```

No es obligatorio usar YAML si una solución Python tipada es más sencilla.

Lo importante es desacoplar:

* extracción;
* normalización;
* validación;
* presentación.

---

# Benchmark

Crear un pequeño sistema de benchmark antes de optimizar modelos o prompts.

Preparar aproximadamente 20-30 páginas representativas:

* estadillos fáciles;
* estadillos difíciles;
* texto manuscrito;
* croquis;
* páginas mixtas.

Guardar una verdad de referencia manual.

Para estadillos medir:

```text
row_precision
row_recall
col_exact
fil_exact
species_exact
height_exact
photo_exact
bbch_exact
```

Especialmente importante:

```text
bbch_exact
```

Comparar inicialmente:

```text
Unlimited-OCR solo

vs.

Unlimited-OCR
+
Qwen 3.5 9B verificando imagen + OCR
```

No optimizar basándose sólo en impresión visual.

---

# Tests

Añadir tests unitarios para:

* renderizado Markdown;
* parsing de schemas;
* normalización de especies;
* detección de duplicados;
* secuencias de filas;
* validación BBCH;
* merge multipágina.

Añadir fixtures pequeños anonimizados cuando sea posible.

Las llamadas reales a LM Studio no deben ser necesarias para ejecutar todos los tests.

La capa del VLM debe poder mockearse.

---

# Compatibilidad

Mantener inicialmente la CLI antigua o proporcionar una migración clara.

La CLI futura ideal podría ser:

```bash
python -m fieldnotes documento.pdf \
    --profile estadillo \
    --vision-model "<qwen-3.5-9b>" \
    --output output/
```

o:

```bash
python -m fieldnotes documento.pdf \
    --profile notebook \
    --vision-model "<qwen-3.5-9b>" \
    --output output/
```

---

# No objetivos iniciales

No intentar todavía:

* RAG;
* embeddings;
* base vectorial;
* interfaz web;
* aplicación móvil;
* múltiples VLM trabajando juntos;
* generación automática de coordenadas GPS;
* corrección automática agresiva;
* reconstrucción visual perfecta del PDF.

Primero conseguir una extracción fiable, trazable y validable.

---

# Prioridades

Orden de prioridad:

1. preservar evidencia;
2. evitar datos inventados;
3. extracción correcta;
4. detectar incertidumbre;
5. salida estructurada;
6. validación;
7. reproducibilidad;
8. Markdown legible;
9. rendimiento.

No sacrificar precisión para conseguir una salida aparentemente limpia.

---

# Plan de implementación recomendado

## PR 1

Refactorizar `agent.py` en módulos sin cambiar comportamiento externo.

Añadir tests básicos.

## PR 2

Introducir `PageArtifact` y artefactos OCR por página.

## PR 3

Mover Unlimited-OCR a worker/proceso separado para liberar VRAM al terminar.

## PR 4

Implementar cliente multimodal LM Studio para Qwen 3.5 9B.

Añadir `ask_vision()`.

## PR 5

Introducir Pydantic schemas y structured outputs.

## PR 6

Implementar perfil `estadillo`.

JSON → merge → validators → Markdown determinista.

## PR 7

Añadir `ReviewIssue`, warnings y crops de revisión.

## PR 8

Crear benchmark y métricas para estadillos.

Ajustar prompts únicamente después de medir.

## PR 9

Introducir `DiagramIR`.

## PR 10

Implementar flowcharts mediante Mermaid y croquis de campo mediante SVG.

## PR 11

Implementar perfil general `notebook`.

---

# Forma de trabajar

Antes de modificar código:

1. inspecciona el repositorio completo;
2. identifica dependencias entre funciones;
3. propone el plan concreto del PR actual;
4. evita reescribir todo de una sola vez;
5. mantén cambios pequeños y verificables;
6. ejecuta tests después de cada refactor relevante.

Si detectas una decisión arquitectónica dudosa, explica primero las alternativas y sus consecuencias.

No elimines funcionalidad actual útil sin justificarla.

No actualices dependencias mayores únicamente porque exista una versión más reciente; comprueba primero compatibilidad con Unlimited-OCR.

---

# Primera tarea concreta

Empieza únicamente por **PR 1: refactor seguro**.

Objetivo:

* dividir responsabilidades de `agent.py`;
* mantener el comportamiento actual;
* mantener la CLI actual;
* añadir tests básicos;
* preparar interfaces para `PageArtifact`, OCR worker y LM Studio, pero sin implementar todavía toda la nueva arquitectura.

Antes de escribir código, presenta:

1. análisis del estado actual;
2. estructura propuesta;
3. archivos que modificarás/crearás;
4. riesgos;
5. criterios de aceptación.

Después realiza la implementación.
