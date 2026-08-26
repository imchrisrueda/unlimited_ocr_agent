class LMStudioClient:
    def __init__(self, base_url: str, api_key: str, default_model: str = None):
        from openai import OpenAI
        # Cliente OpenAI para conectar con el servidor local de LM Studio
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key
        )
        self.model = default_model

    def resolve_model(self) -> str:
        """Obtiene el identificador de un modelo de texto disponible en LM Studio."""
        if self.model:
            return self.model

        models = self.client.models.list().data
        candidates = [
            model.id for model in models
            if "embedding" not in model.id.lower()
        ]
        if not candidates:
            raise RuntimeError(
                "LM Studio no anuncia ningún modelo de texto en /v1/models. "
                "Carga un modelo compatible y vuelve a intentarlo."
            )
        self.model = candidates[0]
        print(f"Modelo seleccionado en LM Studio: {self.model}")
        return self.model

    def ask(
        self,
        document_text: str,
        question: str,
        reasoning_effort: str = "none",
        max_tokens: int = 2048,
    ) -> str:
        """Envía el contenido del documento extraído a LM Studio para análisis."""
        print("Consultando al modelo en LM Studio...")
        system_prompt = (
            "Eres un agente IA especializado en analizar y digitalizar documentos procesados por OCR. "
            "Responde de forma precisa, limpia y bien estructurada en formato Markdown."
        )
        user_prompt = (
            f"=== DOCUMENTO EXTRAÍDO POR UNLIMITED-OCR ===\n"
            f"{document_text}\n"
            f"=============================================\n\n"
            f"INSTRUCCIÓN DEL USUARIO: {question}"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.resolve_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                print(
                    "Ayuda: LM Studio devolvió content vacío. El modelo puede haber consumido "
                    "la salida en razonamiento. Se ha solicitado reasoning_effort='none'; "
                    "si persiste, usa un modelo no razonador o aumenta max_tokens."
                )
                return ""
            return content
        except Exception as e:
            return f"Error al conectar con LM Studio: {e}\nAsegúrate de haber activado el 'Local Server' en LM Studio."

    def ask_chunked(
        self,
        document_text: str,
        question: str,
        chunk_size: int,
        chunk_overlap: int,
        reasoning_effort: str,
        max_tokens: int,
    ) -> str:
        """Resume documentos largos en fragmentos y sintetiza el resultado."""
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
