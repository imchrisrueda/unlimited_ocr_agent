# Guía de análisis con Qwen

Esta guía define cómo pedir a un Qwen local que interprete una o varias sesiones ya digitalizadas. No forma parte de la extracción visual y no autoriza a modificar los datos.

## Cuadernos de campo con revisión humana

En una sesión `cuaderno_campo`, la jerarquía de autoridad es:

1. `reviewed/notas.md` y `reviewed/document.json`: revisión humana publicada.
2. `pages/page_NNN.*`: evidencia visual primaria para verificaciones puntuales.
3. `cuaderno_campo.md`, `document.json`, `raw/` y `assets/`: extracción y reconstrucciones automáticas.

Si `reviewed/` no existe, el modelo debe indicar que trabaja con una extracción no aprobada. Los diagramas SVG y Mermaid ayudan a navegar el contenido, pero cualquier conclusión espacial se verifica contra la imagen original.

`reviewed/document.json` contiene `reviewed_markdown_sha256`, procedencia por página, secciones indexadas y `accepted_uncertainties`. El modelo debe conservar esas incertidumbres y citar `page_number` y `source_image`.

Durante la extracción automática, `cuaderno_campo.md` puede incluir una `Interpretación espacial propuesta por el VLM`: es una ayuda visual editable y no es una fuente autorizada. Tras corregirla en `review/transcripcion.md` y publicar, pasa a `pages[*].spatial_interpretation`, que contiene una interpretación espacial revisada por una persona. Es el resumen adecuado para razonar sobre un croquis; aun así, el modelo debe citar su página y consultar la imagen si la pregunta exige un detalle visual que esa interpretación no declara.

Prompt recomendado para este perfil:

```text
Esta es una sesión cuaderno_campo. Comprueba primero si existe reviewed/.
Si existe, usa reviewed/notas.md y reviewed/document.json como transcripción autorizada.
Consulta la imagen indicada por source_image cuando la respuesta dependa de escritura,
posición, flechas, recuadros o croquis. No uses SVG, Mermaid, OCR bruto ni el JSON raíz
como evidencia primaria. Conserva accepted_uncertainties, no completes ausencias y cita
el número de página que respalda cada afirmación. Si reviewed/ no existe, advierte que la
sesión aún no ha sido aprobada por una persona.
```

## Unidad documental

Cada carpeta representa una sesión. `notas.md` contiene los metadatos y declara `datos: "datos.csv"`; esa referencia vincula el contexto cualitativo con los registros tabulares de la misma carpeta. El nombre ISO de la carpeta identifica la fecha solo cuando fue demostrada por la fuente.

## Reglas para el modelo

1. Trata `notas.md` y `datos.csv` de una misma carpeta como dos vistas de una sesión.
2. Conserva la procedencia: toda conclusión debe indicar la fecha y el archivo que la respaldan.
3. Separa claramente:
   - hechos explícitos;
   - cálculos reproducibles sobre el CSV;
   - inferencias o hipótesis.
4. No supongas que un `id`, una posición o una especie representa el mismo individuo, parcela o experimento en fechas distintas.
5. Solo vincula sesiones cuando los metadatos o una configuración externa declaren esa continuidad.
6. No completes ausencias ni conviertas `null` o celdas vacías en cero.
7. Señala conflictos, cambios de esquema y sesiones con revisión pendiente.
8. Cuando compares alturas, BBCH, recuentos o incidencias, explica el conjunto filtrado y la operación realizada.

## Prompt recomendado

```text
Analiza las sesiones proporcionadas usando la carpeta como unidad documental.
Vincula notas.md con el datos.csv declarado por su campo "datos".
Distingue hechos, cálculos e inferencias; cita fecha y archivo para cada conclusión.
No asumas continuidad entre fechas o identidades por coincidencia de id, col o fil.
No inventes valores ausentes. Señala ambigüedades, conflictos e issues pendientes.
```

## Preparación del contexto

El operador puede aportar una sesión o un conjunto de sesiones a la interfaz local que utilice Qwen. Debe mantener visibles las rutas relativas y no mezclar filas sin su fecha de origen. Esta versión del CLI digitaliza documentos; no incorpora por sí sola un indexador de carpetas ni un motor longitudinal.
