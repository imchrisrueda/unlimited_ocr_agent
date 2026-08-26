import base64
import os
from pathlib import Path
from typing import Optional, Literal, Any, TypeVar, Type
import openai
from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    APIError,
    NotFoundError,
)
from pydantic import BaseModel
from ..config import (
    get_lm_studio_vision_model,
    get_lm_studio_text_model,
    get_lm_studio_legacy_model,
    get_lm_studio_timeout,
)
from .errors import (
    LMStudioError,
    LMStudioConnectionError,
    LMStudioModelNotConfiguredError,
    LMStudioModelNotFoundError,
    LMStudioResponseError,
    LMStudioEmptyResponseError,
    LMStudioResponseTruncatedError,
    StructuredOutputError,
    StructuredOutputParseError,
    StructuredOutputValidationError,
    sanitize_message,
)
from .structured import generate_json_schema, parse_structured_json

T = TypeVar("T", bound=BaseModel)


def encode_image_to_data_uri(image_path: str | Path, max_bytes: int = 20 * 1024 * 1024) -> str:
    """Valida y codifica un archivo de imagen regular en un Data URI base64 seguro.

    Valida existencia, archivo regular, límite de tamaño (positivo) y coincidencia estricta de firmas mágicas:
    - PNG: .png con firma \x89PNG\r\n\x1a\n
    - JPEG: .jpg/.jpeg con firma \xff\xd8\xff
    - WEBP: .webp con firma RIFF....WEBP

    Nunca expone el contenido base64 completo en logs ni excepciones.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError(f"max_bytes debe ser un entero positivo, recibido: {max_bytes!r}")

    path = Path(image_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"El archivo de imagen no existe o no es un archivo regular: {path.name}")

    ext = path.suffix.lower()
    valid_exts = (".png", ".jpg", ".jpeg", ".webp")
    if ext not in valid_exts:
        raise ValueError(f"Extensión de imagen no soportada '{ext}'. Extensiones válidas: {valid_exts}")

    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise OSError(f"Error al obtener el tamaño del archivo '{path.name}': {sanitize_message(str(exc))}") from exc

    if file_size > max_bytes:
        raise ValueError(
            f"El archivo de imagen supera el tamaño máximo permitido de {max_bytes} bytes "
            f"(tamaño real: {file_size} bytes): {path.name}"
        )

    try:
        with open(path, "rb") as f:
            header = f.read(32)
            if ext == ".png":
                if not header.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError(f"Firma mágica inválida para archivo con extensión .png: {path.name}")
                mime = "image/png"
            elif ext in (".jpg", ".jpeg"):
                if not header.startswith(b"\xff\xd8\xff"):
                    raise ValueError(f"Firma mágica inválida para archivo con extensión {ext}: {path.name}")
                mime = "image/jpeg"
            elif ext == ".webp":
                if len(header) < 12 or not (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
                    raise ValueError(f"Firma mágica inválida para archivo con extensión .webp: {path.name}")
                mime = "image/webp"
            else:
                raise ValueError(f"Extensión no soportada: {ext}")

            f.seek(0)
            data = f.read()
    except (FileNotFoundError, ValueError):
        raise
    except OSError as exc:
        raise OSError(f"Error al leer el archivo de imagen '{path.name}': {sanitize_message(str(exc))}") from exc

    encoded_b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded_b64}"


class LMStudioClient:
    """Cliente tipado para LM Studio compatible con la API de OpenAI (texto y visión multimodal)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        vision_model: Optional[str] = None,
        text_model: Optional[str] = None,
        timeout: Optional[int] = None,
        default_model: Optional[str] = None,
        validate_model: bool = True,
    ):
        from openai import OpenAI

        self.timeout = timeout if timeout is not None else get_lm_studio_timeout()
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=self.timeout,
        )
        self._vision_model = vision_model
        self._text_model = text_model
        self._default_legacy_model = default_model
        self.validate_model = validate_model
        self._validated_models: set[str] = set()

    @property
    def vision_model(self) -> Optional[str]:
        return self._vision_model

    @vision_model.setter
    def vision_model(self, value: Optional[str]) -> None:
        self._vision_model = value
        self._validated_models.clear()

    @property
    def text_model(self) -> Optional[str]:
        return self._text_model

    @text_model.setter
    def text_model(self, value: Optional[str]) -> None:
        self._text_model = value
        self._validated_models.clear()

    @property
    def model(self) -> Optional[str]:
        """Propiedad retrocompatible para consultar o modificar el modelo de texto."""
        return (
            self._text_model
            or self._default_legacy_model
            or get_lm_studio_text_model()
            or get_lm_studio_legacy_model()
        )

    @model.setter
    def model(self, value: Optional[str]) -> None:
        self.text_model = value
        self._default_legacy_model = value

    def _fetch_advertised_model_ids(self) -> list[str]:
        """Consulta /v1/models mapeando excepciones a tipos específicos sin tragarlas."""
        try:
            models_data = self.client.models.list().data
            return [m.id for m in models_data]
        except (APIConnectionError, APITimeoutError, TimeoutError, ConnectionError) as exc:
            raise LMStudioConnectionError(
                f"Fallo al conectar con LM Studio en '{self.client.base_url}' para consultar /v1/models: {sanitize_message(str(exc))}"
            ) from exc
        except NotFoundError as exc:
            raise LMStudioModelNotFoundError(
                f"Endpoint de modelos no encontrado en LM Studio: {sanitize_message(str(exc))}"
            ) from exc
        except (APIStatusError, APIError, openai.OpenAIError) as exc:
            raise LMStudioResponseError(
                f"Error de respuesta de LM Studio al listar /v1/models: {sanitize_message(str(exc))}"
            ) from exc
        except Exception as exc:
            raise LMStudioResponseError(
                f"Error inesperado al consultar modelos en LM Studio ({type(exc).__name__}): {sanitize_message(str(exc))}"
            ) from exc

    def resolve_model(
        self,
        model_type: Literal["vision", "text"] = "vision",
        requested_model: Optional[str] = None,
    ) -> str:
        """Resuelve el identificador de modelo según la precedencia estricta documentada.

        Precedencia exacta:
        1. requested_model (argumento explícito por llamada)
        2. self.vision_model / self.text_model (configurado en constructor / propiedad)
        3. LM_STUDIO_VISION_MODEL / LM_STUDIO_TEXT_MODEL (variables de entorno específicas)
        4. self._default_legacy_model (argumento legacy default_model / lm_model)
        5. LM_STUDIO_MODEL (variable de entorno legacy)

        Si no hay modelo configurado, lanza LMStudioModelNotConfiguredError con diagnóstico.
        No auto-selecciona modelos. Valida la existencia del modelo contra /v1/models una sola vez y lo cachea.
        """
        candidate: Optional[str] = None

        if requested_model and requested_model.strip():
            candidate = requested_model.strip()
        elif model_type == "vision":
            candidate = (
                self._vision_model
                or get_lm_studio_vision_model()
                or self._default_legacy_model
                or get_lm_studio_legacy_model()
            )
        elif model_type == "text":
            candidate = (
                self._text_model
                or get_lm_studio_text_model()
                or self._default_legacy_model
                or get_lm_studio_legacy_model()
            )

        if not candidate:
            advertised: list[str] = []
            diag = ""
            try:
                advertised = self._fetch_advertised_model_ids()
                diag = f" Modelos anunciados en LM Studio: {advertised}" if advertised else " LM Studio no anunció ningún modelo."
            except LMStudioError as err:
                diag = f" (No se pudo consultar /v1/models: {err})"
            env_var = "LM_STUDIO_VISION_MODEL" if model_type == "vision" else "LM_STUDIO_TEXT_MODEL"
            raise LMStudioModelNotConfiguredError(
                f"No se ha configurado ningún modelo de {model_type} para LM Studio. "
                f"Establece la variable de entorno {env_var} o LM_STUDIO_MODEL, "
                f"pásalo en el constructor ({model_type}_model) o especifícalo en la llamada.{diag}"
            )

        if self.validate_model and candidate not in self._validated_models:
            advertised = self._fetch_advertised_model_ids()
            if not advertised:
                raise LMStudioModelNotFoundError(
                    f"LM Studio no tiene ningún modelo cargado o anunciado en /v1/models para validar '{candidate}'."
                )
            if candidate not in advertised:
                raise LMStudioModelNotFoundError(
                    f"El modelo configurado '{candidate}' ({model_type}) no se encuentra cargado ni anunciado en LM Studio. "
                    f"Modelos disponibles: {advertised}"
                )
            self._validated_models.add(candidate)

        return candidate

    def _execute_chat_completion(self, kwargs: dict[str, Any], resolved_model: str) -> str:
        """Ejecuta la llamada de chat completion con mapeo determinista de excepciones SDK."""
        try:
            response = self.client.chat.completions.create(**kwargs)
            choice_obj = response.choices[0]
            message = choice_obj.message
            content = message.content or ""
            if not content.strip():
                raise LMStudioEmptyResponseError(
                    "LM Studio devolvió una respuesta con contenido vacío. "
                    "El modelo puede haber consumido los tokens en razonamiento interno."
                )
            if getattr(choice_obj, "finish_reason", None) == "length":
                raise LMStudioResponseTruncatedError(
                    f"La respuesta de LM Studio fue truncada por límite de tokens (finish_reason='length', "
                    f"max_tokens={kwargs.get('max_tokens')}). El contenido JSON está incompleto.",
                    raw_response=content,
                )
            return content
        except (APIConnectionError, APITimeoutError, TimeoutError, ConnectionError) as exc:
            raise LMStudioConnectionError(
                f"Fallo al conectar con LM Studio en '{self.client.base_url}': {sanitize_message(str(exc))}"
            ) from exc
        except NotFoundError as exc:
            raise LMStudioModelNotFoundError(
                f"Modelo '{resolved_model}' no encontrado o no disponible en LM Studio: {sanitize_message(str(exc))}"
            ) from exc
        except (APIStatusError, APIError, openai.OpenAIError) as exc:
            raise LMStudioResponseError(
                f"Error en respuesta de LM Studio ({type(exc).__name__}): {sanitize_message(str(exc))}"
            ) from exc
        except (LMStudioError, FileNotFoundError, ValueError, OSError):
            raise
        except Exception as exc:
            raise LMStudioResponseError(
                f"Error inesperado en llamada a LM Studio ({type(exc).__name__}): {sanitize_message(str(exc))}"
            ) from exc

    def ask_text(
        self,
        prompt: str,
        context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: str = "none",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        response_format: Optional[dict[str, Any]] = None,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> str:
        """Envía una instrucción y contexto textual a LM Studio."""
        resolved_model = self.resolve_model(model_type="text", requested_model=model)

        sys_prompt = system_prompt or (
            "Eres un agente IA especializado en analizar y digitalizar documentos procesados por OCR. "
            "Responde de forma precisa, limpia y bien estructurada en formato Markdown."
        )

        if context:
            user_content = (
                f"=== DOCUMENTO EXTRAÍDO POR UNLIMITED-OCR ===\n"
                f"{context}\n"
                f"=============================================\n\n"
                f"INSTRUCCIÓN DEL USUARIO: {prompt}"
            )
        else:
            user_content = prompt

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_content},
        ]

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if reasoning_effort and reasoning_effort != "none":
            kwargs["reasoning_effort"] = reasoning_effort
        if response_format is not None:
            kwargs["response_format"] = response_format
        if extra_body is not None:
            kwargs["extra_body"] = extra_body

        return self._execute_chat_completion(kwargs, resolved_model)

    def ask_vision(
        self,
        image_path: str | Path,
        prompt: str,
        ocr_context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: str = "none",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        response_format: Optional[dict[str, Any]] = None,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> str:
        """Envía una imagen (fuente primaria de evidencia) junto a OCR complementario a LM Studio."""
        data_uri = encode_image_to_data_uri(image_path)
        resolved_model = self.resolve_model(model_type="vision", requested_model=model)

        sys_prompt = system_prompt or (
            "Eres un agente IA multimodal especializado en digitalizar documentos y notas de campo. "
            "La imagen suministrada es la fuente primaria de evidencia y verdad. "
            "El texto OCR complementario es solo una hipótesis de apoyo que puede contener errores. "
            "Responde de forma precisa, limpia y fiel a lo visible en la imagen."
        )

        if ocr_context:
            text_prompt = (
                f"=== TEXTO OCR DE APOYO (HIPÓTESIS) ===\n"
                f"{ocr_context}\n"
                f"=======================================\n\n"
                f"INSTRUCCIÓN DEL USUARIO: {prompt}"
            )
        else:
            text_prompt = prompt

        user_content = [
            {"type": "text", "text": text_prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_content},
        ]

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if reasoning_effort and reasoning_effort != "none":
            kwargs["reasoning_effort"] = reasoning_effort
        if response_format is not None:
            kwargs["response_format"] = response_format
        if extra_body is not None:
            kwargs["extra_body"] = extra_body

        return self._execute_chat_completion(kwargs, resolved_model)

    def ask_text_structured(
        self,
        prompt: str,
        schema: Type[T],
        context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: str = "none",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> T:
        """Envía una consulta textual estructurada y devuelve una instancia validada del esquema Pydantic."""
        schema_format = generate_json_schema(schema)
        raw_res = self.ask_text(
            prompt=prompt,
            context=context,
            system_prompt=system_prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=schema_format,
            extra_body=extra_body,
        )
        return parse_structured_json(raw_res, schema)

    def ask_vision_structured(
        self,
        image_path: str | Path,
        prompt: str,
        schema: Type[T],
        ocr_context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: str = "none",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> T:
        """Envía una consulta visual estructurada y devuelve una instancia validada del esquema Pydantic."""
        schema_format = generate_json_schema(schema)
        raw_res = self.ask_vision(
            image_path=image_path,
            prompt=prompt,
            ocr_context=ocr_context,
            system_prompt=system_prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=schema_format,
            extra_body=extra_body,
        )
        return parse_structured_json(raw_res, schema)

    def ask(
        self,
        document_text: str,
        question: str,
        reasoning_effort: str = "none",
        max_tokens: int = 2048,
    ) -> str:
        """Alias retrocompatible para análisis de texto extraído."""
        return self.ask_text(
            prompt=question,
            context=document_text,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )

    def ask_chunked(
        self,
        document_text: str,
        question: str,
        chunk_size: int,
        chunk_overlap: int,
        reasoning_effort: str,
        max_tokens: int,
    ) -> str:
        """Resume documentos largos en fragmentos y sintetiza el resultado (retrocompatibilidad)."""
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap debe ser menor que chunk_size")

        chunks = []
        start = 0
        while start < len(document_text):
            end = min(start + chunk_size, len(document_text))
            chunks.append(document_text[start:end])
            if end == len(document_text):
                break
            start = end - chunk_overlap

        partials = []
        for index, chunk in enumerate(chunks, start=1):
            print(f"Analizando fragmento {index}/{len(chunks)}...")
            partial = self.ask(
                chunk,
                "Analiza únicamente este fragmento y extrae los datos relevantes para la tarea. "
                + question,
                reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
            )
            if partial.strip():
                partials.append(f"### Fragmento {index}\n{partial}")

        if not partials:
            return ""

        print("Sintetizando los resultados parciales...")
        return self.ask(
            "\n\n".join(partials),
            "Combina los análisis parciales en una respuesta única, coherente y fiel al documento. "
            + question,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )


__all__ = [
    "LMStudioClient",
    "encode_image_to_data_uri",
    "generate_json_schema",
    "parse_structured_json",
    "LMStudioError",
    "LMStudioConnectionError",
    "LMStudioModelNotConfiguredError",
    "LMStudioModelNotFoundError",
    "LMStudioResponseError",
    "LMStudioEmptyResponseError",
    "StructuredOutputError",
    "StructuredOutputParseError",
    "StructuredOutputValidationError",
    "sanitize_message",
]
