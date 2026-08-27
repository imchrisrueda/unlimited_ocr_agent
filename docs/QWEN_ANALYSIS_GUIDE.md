# Guía de análisis con Qwen

Esta guía define cómo pedir a un Qwen local que interprete una o varias sesiones ya digitalizadas. No forma parte de la extracción visual y no autoriza a modificar los datos.

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
