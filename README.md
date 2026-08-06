# 🤖 Agente IA: Unlimited-OCR + LM Studio

Una solución 100% local, privada y automatizada para la digitalización, extracción y análisis inteligente de documentos (PDFs o imágenes) combinando el modelo multimodal de visión **Baidu Unlimited-OCR** con **LM Studio** como motor de razonamiento de lenguaje.

---

## ⚡ Detección Automática de Hardware (GPU / CPU)

El código del proyecto está diseñado con **detección y selección dinámica de hardware**:
* **Con GPU NVIDIA disponible**: El agente utilizará automáticamente la tarjeta gráfica acelerada por **CUDA 12.4** (`bfloat16` / `float16`), maximizando la velocidad de procesamiento.
* **Sin GPU en el sistema**: El agente utilizará automáticamente la CPU (`float32`) como respaldo sin necesidad de cambiar ninguna línea de código.

---

## 🚀 Instalación y Configuración del Entorno

### 1. Requisitos Previos
* **Python 3.10 - 3.12**
* **`uv`** (Administrador rápido de entornos Python)
* **LM Studio** (Opcional, si deseas que el Agente procese o responda preguntas sobre los documentos)

### 2. Configuración Automática del Entorno

Abre PowerShell en el directorio del proyecto y ejecuta:

```powershell
# 1. Crear el entorno virtual con uv
uv venv .venv

# 2. Activar el entorno virtual (PowerShell)
.\.venv\Scripts\Activate.ps1

# 3. Ejecutar el configurador automático de entorno (detecta GPU/CPU e instala lo necesario)
python setup_env.py
```

---

## 🔌 Conectar con LM Studio

1. Abre **LM Studio** y carga tu modelo LLM preferido (ej. `Qwen 2.5 7B/14B`, `Llama 3.1 8B`, `DeepSeek R1`).
2. Entra en el panel lateral **Developer / Local Server** (icono `</>`).
3. Haz clic en **Start Server**. Verifica que esté escuchando en `http://localhost:1234`.

---

## 📖 Ejemplos de Uso

### 📄 1. Digitalizar directamente a PDF (Sin escribir mensaje)
Utiliza la instrucción por defecto (*Digitaliza este documento manteniendo su estructura en Markdown limpio*), procesa con LM Studio y exporta a un archivo PDF:
```powershell
python agent.py documento.png --export-pdf salida.pdf
```

### 📝 2. Digitalizar directamente a Markdown (.md)
Digitaliza el documento y guarda la información estructurada en un archivo Markdown:
```powershell
python agent.py documento.pdf --export-md digitalizado.md
```

### ❓ 3. Digitalizar con consulta o instrucción personalizada
Realiza preguntas o pide resúmenes específicos sobre el contenido del documento:
```powershell
python agent.py contrato.pdf "Resume las obligaciones de las partes y las fechas de vencimiento" --export-pdf resumen.pdf
```

### ⚡ 4. Extracción directa (OCR Puro) sin pasar por LM Studio (`--raw`)
Extrae directamente el texto mediante Unlimited-OCR omitiendo la consulta a LM Studio:
```powershell
# Exportar a Markdown
python agent.py imagen.jpg --raw --export-md ocr_puro.md

# Exportar a PDF
python agent.py imagen.jpg --raw --export-pdf ocr_puro.pdf
```

### 📑 5. Exportación simultánea a MD y PDF
```powershell
python agent.py informe.pdf --export-md informe.md --export-pdf informe.pdf
```

---

## 📂 Estructura del Repositorio

```
unlimited_ocr_agent/
├── agent.py            # Script principal del Agente (OCR + LM Studio + Exportador)
├── setup_env.py        # Detección y configuración automática de entorno (GPU CUDA vs CPU)
├── requirements.txt    # Dependencias del proyecto
└── README.md           # Documentación del proyecto
```
