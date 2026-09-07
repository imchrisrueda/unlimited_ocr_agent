# Risks

Status: active

## Register

| ID | Category | Risk | Likelihood | Impact | Mitigation | Owner | Status |
|---|---|---|---|---|---|---|---|
| R-01 | Hardware | Competición por memoria VRAM al cargar OCR y VLM simultáneamente. | Alta | Alto | Implementar procesamiento por fases estricto, descargando modelos. | Equipo | Activo |
| R-02 | Calidad | Pérdida de datos originales o "alucinaciones" al extraer. | Media | Alto | Limitar creatividad del VLM. Mantener evidencia `raw`, página de origen y flag de incertidumbre en DocumentIR. | Equipo | Activo |
| R-03 | Integración | Romper implementaciones existentes durante el refactor. | Media | Medio | Mantener `agent.py` y `UnlimitedOCRAgent` como wrappers de la nueva arquitectura. | Equipo | Activo |
| R-04 | Precisión | El OCR inicial introduce errores en columnas/coordenadas. | Alta | Medio | Usar el OCR solo como hint; instruir al VLM para que la imagen sea la fuente de verdad prioritaria. | Equipo | Activo |
