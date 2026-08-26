# Architectural Decision Records (ADRs)

Este directorio contiene el registro inmutable de decisiones arquitectónicas del proyecto, siguiendo el perfil SOFTWARE de Jarvis.

## Decisiones registradas

- Las decisiones deben añadirse como nuevos archivos Markdown (ej. `0001-nombre.md`).
- El estado de la decisión puede ser: `propuesta`, `aceptada`, `rechazada`, `obsoleta`.

## Decisiones iniciales

- **Procesamiento por fases**: Se ha decidido descargar de la GPU el proceso de OCR antes de invocar LM Studio.
- **Imagen como fuente primaria**: El VLM evaluará las imágenes antes de confiar en el resultado textual de Unlimited-OCR, para subsanar los defectos del OCR en layouts tabulares complejos.
- **DocumentIR**: Se ha decidido evitar que el VLM emita Markdown directamente; en su lugar, se emitirá JSON Schema Pydantic que un pipeline en Python procesará para emitir el Markdown final.
