# Project brief

Status: active

## Problem
El proyecto actual digitaliza notas de campo usando Unlimited-OCR y Qwen 3.5 9B, pero existen errores al extraer datos manuscritos (columnas desplazadas, confusión de campos) y el modelo de lenguaje (LM Studio) solo recibe el texto OCR sin acceso a la imagen original. Además, ambos modelos compiten por los 8 GB de VRAM de la GPU, lo que requiere un procesamiento por fases. Todo el código está concentrado en `agent.py`.

## Objective
Crear un sistema de digitalización de cuadernos de campo en Markdown diseñado para lectura humana y análisis posterior. El flujo debe considerar la imagen original como fuente primaria de evidencia, usando el OCR solo como hipótesis. Se requiere un procesamiento por fases para no exceder la memoria VRAM y una arquitectura estructurada (`src/fieldnotes`) separando responsabilidades (ingesta, OCR, VLM, validación, renderizado).

## Users and stakeholders
- Investigadores y técnicos agrícolas.
- Analistas de datos.

## Scope
- Digitalización de notas manuscritas, tablas, registros fenológicos BBCH, croquis y diagramas.
- Refactorización de la base de código actual (`agent.py`) en módulos especializados.
- Implementación de ejecuciones por fases (rasterizar -> OCR -> descargar OCR -> cargar VLM -> estructurar y renderizar).
- Pruebas unitarias sin dependencias pesadas (GPU, modelos, red).

## Out of scope
- Generación de Markdown definitivo directamente por el VLM.
- Inventar datos ausentes o ilegibles.
- Implementar perfiles de croquis complejos en la fase inicial (primero estadillos).

## Requirements
- Separar `agent.py` en configuración, ingesta PDF, OCR, LM Studio, exportación, pipeline y CLI.
- Conservar retrocompatibilidad: `python agent.py` y `from agent import UnlimitedOCRAgent`.
- Conservar métodos públicos y argumentos por defecto.
- Interfaz explícita configurable para LM Studio.
- Salida estructurada mediante JSON Schema y representación intermedia (`DocumentIR`).

## Constraints
- Hardware principal: NVIDIA RTX 4070 Ti, 8 GB VRAM.
- Procesamiento estrictamente local.
- Un solo VLM (Qwen 3.5 9B) inicialmente.

## Data
- PDFs escaneados y fotografías.

## Inputs and outputs
- Entradas: PDF o imágenes.
- Salidas: Cuaderno de campo estructurado en Markdown, JSON de representación intermedia (IR), imágenes originales/crops.

## Security and privacy
- Procesamiento offline (local) garantiza la privacidad de los datos experimentales.

## Success criteria
- Ejecución exitosa por fases sin superar los límites de VRAM.
- Validación estructurada de estadillos.
- Alta precisión en la digitalización de manuscritos mediante VLM validando la imagen original.
- Cobertura de pruebas unitarias portables.

## Open questions
- Evaluación del rendimiento del proceso por página vs inferencia múltiple de Unlimited-OCR.
