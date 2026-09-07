# Project plan

Status: active

## Milestones
1. **Refactor seguro de agent.py (Fase actual)**: Separar la lógica actual en `src/fieldnotes` sin romper la compatibilidad de CLI y API.
2. **Implementación de artefactos y DocumentIR**: Cambiar unidad de procesamiento de documento completo a página, y definir esquemas Pydantic y JSON Schema.
3. **Perfil Estadillo**: Flujo completo para extracción, validación y renderizado estructurado de tablas (estadillos).
4. **Sistema de revisión y crops**: Capacidad de generar alertas e imágenes de recortes para análisis en segunda pasada de las regiones ambiguas.
5. **Procesamiento de diagramas (Fase futura)**: Implementación de croquis espaciales y renderizado SVG/Mermaid.
6. **Perfil cuaderno general**: Combinación de notas libres, estadillos y diagramas en un documento maestro.

## Dependencies
- Refactor (Milestone 1) es prerrequisito estricto de cualquier otra adición.
- Perfil Estadillo precede a Perfil Cuaderno.

## Validation gates
- Unit tests portables funcionales en cada etapa.
- Retrocompatibilidad comprobada para la primera fase de refactor.

## Open decisions
- Determinar si el OCR operará sistemáticamente página por página, o si se mantendrá `infer_multi` gestionando la correspondencia a posteriori.
