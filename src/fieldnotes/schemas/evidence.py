from typing import Optional, Generic, TypeVar
from pydantic import BaseModel, Field, ConfigDict, model_validator

T = TypeVar("T")


class EvidenceValue(BaseModel, Generic[T]):
    """Contenedor estricto para preservar evidencia empírica de un dato extraído.

    Separa el valor original leído ('raw') del valor tipado ('normalized'),
    registrando la página fuente obligatoria ('source_page' >= 1), el estado
    de incertidumbre ('uncertain') y lecturas alternativas visuales ('alternatives').
    """
    raw: Optional[str] = None
    normalized: Optional[T] = None
    source_page: int = Field(..., ge=1)
    uncertain: bool = False
    alternatives: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_evidence_structure(self) -> "EvidenceValue[T]":
        """Valida las invariantes estructurales de la evidencia."""
        if not self.uncertain:
            if self.raw is None and self.normalized is None:
                raise ValueError(
                    "EvidenceValue debe contener al menos 'raw' o 'normalized' cuando uncertain=False. "
                    "Para representar evidencia ilegible o desconocida, establece uncertain=True."
                )
            if self.alternatives:
                raise ValueError(
                    "No se permiten 'alternatives' cuando uncertain=False. "
                    "Las lecturas alternativas solo son válidas para evidencia incierta (uncertain=True)."
                )
        return self
