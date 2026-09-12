"""Walidacja korekt nominalnej wydajności bez dopisywania nieznanej normy pomiaru."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TonerYieldUpdate(BaseModel):
    """Pełna korekta wartości wymagająca wersji rekordu i jawnego uzasadnienia."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    revision: int = Field(ge=0, strict=True)
    pages: int | None = Field(default=None, gt=0, le=2147483647, strict=True)
    status: Literal["confirmed", "estimated", "missing"]
    source: str = Field(default="", max_length=2000)
    basis: str = Field(default="", max_length=2000)
    reason: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def validate_evidence(self):
        """Odróżnia brak wartości, deklarację źródłową i jawny szacunek."""
        if self.status == "missing":
            if self.pages is not None:
                raise ValueError("Brak danych wymaga pustej wydajności.")
        elif self.pages is None or not self.source:
            raise ValueError("Wydajność wymaga liczby stron oraz źródła.")
        if self.status == "estimated" and not self.basis:
            raise ValueError("Szacunek wymaga wskazania podstawy oszacowania.")
        return self
