# AGENTS.md

## 1. Objetivo y Principio de Integridad

Digitalizar, estructurar y normalizar notas de campo agronómicas y forestales (perfil **estadillo**) a partir de fuentes documentales (PDFs escaneados, imágenes manuscritas, OCR) con máxima fidelidad visual.

- **No invención**: Si un dato no figura en la fuente original o es ilegible, no debe inferirse ni inventarse.
- **Ausencias**: Asignar `null` en YAML/JSON y celda vacía en CSV.
- **Sin textos espurios**: No agregar encabezados artificiales ni frases como «sin notas» o «sin observaciones» si la fuente no lo declara explícitamente.

---

## 2. Formato Canónico del Perfil Estadillo

La entrega por sesión se organiza en una carpeta dedicada con dos archivos vinculados:

```text
<directorio_sesion>/
├── notas.md    # Front matter YAML con metadatos + notas cualitativas reales
└── datos.csv   # Registros tabulares en CSV UTF-8
```

### 2.1 Nombre de carpeta de sesión
- Si la fecha de la sesión es inequívoca en la fuente: usar formato ISO `YYYY-MM-DD/` (ejemplo: `2026-05-06/`).
- Si la fecha falta o es ambigua: **no inventar** fecha ISO; usar el nombre seguro derivado del documento (stem seguro) y registrar un issue de revisión.

---

## 3. Especificación de `notas.md`

### 3.1 Front Matter YAML
Debe comenzar al inicio del archivo delimitado por `---`. Utiliza valores `null`, listas vacías `[]` o placeholders neutrales cuando no haya evidencia:

```yaml
---
objetivo: null
fecha: null
asistentes: []
equipamiento: null
situacion_atmosferica: null
especies:
  - "P": null
  - "H": null
  - "R": null
  - "M": null
datos: "datos.csv"
---
```

- `objetivo`: Propósito declarado o `null`.
- `fecha`: Fecha `YYYY-MM-DD` extraída de la fuente o `null`.
- `asistentes`: Lista de participantes o iniciales presentes, o `[]`.
- `equipamiento`: Material o instrumental declarado o `null`.
- `situacion_atmosferica`: Condiciones meteorológicas declaradas o `null`.
- `especies`: Entradas para `P`, `H`, `R`, `M` con descripción `null`, salvo evidencia o configuración autorizada que declare descripciones explícitas.
- `datos`: Referencia contractual obligatoria `"datos.csv"`.

### 3.2 Cuerpo de notas
- `notas.md` puede contener únicamente el front matter si no existen notas adicionales en la fuente.
- Incluir solo las secciones, observaciones, anotaciones al margen o croquis realmente observados en el documento fuente, sin añadir texto de relleno ni encabezados artificiales.

---

## 4. Especificación de `datos.csv`

Archivo CSV codificado en **UTF-8**.

### 4.1 Cabecera exacta obligatoria
```csv
id,col,fil,especie,altura_cm,foto,bbch,observaciones
```

### 4.2 Columnas
- `id`: Identificador de muestra si existe en origen; vacío si no figura.
- `col`: Coordenada o número de columna.
- `fil`: Coordenada o número de fila.
- `especie`: Código normalizado (`P`, `H`, `R`, `M`).
- `altura_cm`: Altura en cm (numérico).
- `foto`: Referencia o número de foto si existe.
- `bbch`: Estado fenológico BBCH.
- `observaciones`: Comentarios o incidencias asociadas al registro.

### 4.3 Reglas de integridad
- Celdas ausentes se dejan vacías (ejemplo: `,`).
- Unificar todas las páginas del documento en un solo CSV continuo.
- Si una observación está partida en varias líneas en el original, unirla en una sola celda.
- Conservar el orden original de lectura.

---

## 5. Normalización de Especies

Aplicar exclusivamente el contrato autorizado:
- `Ap` y variaciones explícitas $\rightarrow$ `P`
- `Ah` y variaciones explícitas $\rightarrow$ `H`
- `Ar` y variaciones explícitas $\rightarrow$ `R`
- `Mz` y variaciones explícitas $\rightarrow$ `M`

Cualquier valor o código no contemplado se preserva en su forma textual original o se marca como dudoso (`uncertain_fields` / `review/issues.json`), sin inventar equivalencias.

---

## 6. Rol del Directorio `gt/`

- `gt/` es únicamente una **referencia de forma y estructura validada** por el usuario.
- **Nunca** debe usarse como fuente de datos para rellenar campos en otras sesiones o documentos. Cada sesión se extrae exclusivamente de su propia fuente.

---

## 7. Guía de Consumo para LLMs

1. **Unidad de sesión**: Cada carpeta es una sesión independiente.
2. **Vinculación**: `notas.md` enlaza formal y explícitamente a su matriz mediante `datos: "datos.csv"`.
3. **Estabilidad semántica**: Nombres, fechas, columnas y códigos (`P`, `H`, `R`, `M`) tienen significado coherente y formal en el dataset.
4. **Comparabilidad temporal**: Es válido contrastar fechas, metadatos y mediciones entre carpetas.
5. **Límites de inferencia**: **No asumir continuidad ni identidad** (mismo experimento, misma parcela o mismo individuo) entre registros o sesiones por coincidencia de estructura o `id`, salvo evidencia textual explícita o configuración autorizada.

---

## 8. Trazabilidad y Auditoría

- Todo dato debe ser trazable a la imagen fuente (`pages/page_XXX.png`).
- Las incertidumbres visuales se declaran en artefactos de auditoría (`uncertain_fields`, `review/issues.json`), manteniendo los entregables canónicos (`notas.md` y `datos.csv`) limpios, precisos y estandarizados.
