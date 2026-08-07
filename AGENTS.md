# AGENTS.md

## Objetivo

Revisar archivos Markdown (`.md`) digitalizados, localizar la información existente y reorganizarla en un formato homogéneo.

No inventar información. Si un dato no aparece en el archivo original, dejar el valor vacío.

## Formato de salida obligatorio

Cada archivo corregido debe comenzar con **dos tablas Markdown**.

### Primera tabla

La primera tabla debe tener **3 columnas y 2 filas de datos**, con este contenido y orden:

| Objetivo | Fecha | Asistentes |
|---|---|---|
| {buscar en el md} | {buscar en el md} | {buscar en el md} |
| Equipamiento: {buscar en el md} | Situación atmosférica: {buscar en el md} | Especies: P;H;R;M |

Reglas:

- En **Objetivo**, extraer del documento el objetivo, finalidad o propósito indicado.
- En **Fecha**, extraer la fecha que figure en el documento.
- En **Asistentes**, extraer los nombres o relación de asistentes.
- En **Equipamiento**, extraer el material o equipamiento mencionado.
- En **Situación atmosférica**, extraer las condiciones meteorológicas o atmosféricas indicadas.
- En **Especies**, escribir siempre exactamente: `P;H;R;M`. Cambia `Ap` y variaciones a `P`, `Ah` y variaciones a `H`, `Ar` y variaciones a `R`, `Mz` y variaciones a `M`.
- Mantener cada dato dentro de su celda.
- No añadir filas ni columnas a esta primera tabla.
- Si un campo no aparece en el documento, dejar su contenido vacío.

### Segunda tabla

Inmediatamente después de la primera tabla, crear una segunda tabla con esta cabecera exacta:

|id|col|fil|especie|altura_cm|foto|bbch|observaciones|
|---|---|---|---|---|---|---|---|

A continuación, trasladar a esta tabla los registros que existan en el Markdown original.

## Reglas para la segunda tabla

- `id`: identificador del registro, si existe.
- `col`: columna o posición de columna.
- `fil`: fila o posición de fila.
- `especie`: especie indicada en el registro.
- `altura_cm`: altura expresada en centímetros.
- `foto`: referencia, nombre o ruta de la fotografía, si existe.
- `bbch`: código o estado BBCH, si existe.
- `observaciones`: cualquier comentario, incidencia, descripción o información adicional asociada al registro.
- Conservar todos los registros encontrados en el archivo original.
- No inventar valores ausentes.
- Si un dato no existe, dejar la celda vacía.
- No eliminar observaciones relevantes.
- Unir todas las páginas en una sola tabla
- Si una misma observación aparece dividida en varias líneas, unirla en una sola celda cuando pertenezca al mismo registro.
- Mantener el orden original de los registros salvo que el documento indique claramente otro orden lógico.
- No modificar nombres de especies, códigos BBCH, identificadores, coordenadas de fila/columna ni referencias de fotografías salvo para corregir errores evidentes de formato.

## Limpieza del documento

Después de reconstruir las dos tablas:

1. Eliminar texto duplicado que ya haya sido incorporado correctamente a las tablas.
2. Conservar cualquier información adicional relevante que no tenga cabida en las tablas, colocándola después de ellas bajo un encabezado `## Información adicional`.
3. Corregir únicamente errores evidentes de Markdown, saltos de línea o espacios producidos por la digitalización.
4. No resumir, reinterpretar ni completar información que no esté presente en el archivo fuente.
5. Mantener el contenido en español salvo que el original utilice otro idioma para un dato concreto.

## Plantilla final

```md
| Objetivo | Fecha | Asistentes |
|---|---|---|
| ... | ... | ... |
| Equipamiento: ... | Situación atmosférica: ... | Especies: P;H;R;M |

|id|col|fil|especie|altura_cm|foto|bbch|observaciones|
|---|---|---|---|---|---|---|---|
|...|...|...|...|...|...|...|...|
```
