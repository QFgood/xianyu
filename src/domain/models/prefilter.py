"""Strict, bounded task-level listing-title policy (literal values only)."""
import logging
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator, model_validator


def normalize_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class ListingPrefilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal[1] = 1
    mode: Literal["off", "shadow", "enforce"] = "off"
    kind: Literal["anchored_title_tokens"] = "anchored_title_tokens"
    product_aliases: list[StrictStr] = Field(default_factory=list, max_length=64)
    reject_tokens: list[StrictStr] = Field(default_factory=list, max_length=64)
    keep_tokens: list[StrictStr] = Field(default_factory=list, max_length=64)
    ambiguity_terms: list[StrictStr] = Field(default_factory=list, max_length=64)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported version")
        return value

    @field_validator("product_aliases", "reject_tokens", "keep_tokens", "ambiguity_terms")
    @classmethod
    def normalize_literals(cls, values):
        result = []
        for value in values:
            if not value or len(value) > 64:
                raise ValueError("invalid literal length")
            text = normalize_title(value)
            if not text or len(text) > 64 or any(unicodedata.category(c).startswith("C") for c in value):
                raise ValueError("invalid literal length or characters")
            if text not in result:
                result.append(text)
        return result

    @model_validator(mode="after")
    def validate_policy(self):
        if self.mode != "off" and (not self.product_aliases or not self.reject_tokens):
            raise ValueError("enabled policy requires product_aliases and reject_tokens")
        if set(self.reject_tokens) & set(self.keep_tokens):
            raise ValueError("reject_tokens and keep_tokens overlap")
        return self


def parse_stored_prefilter(value):
    """Only the optional policy fails open; callers retain their other errors."""
    if value is None:
        return None
    try:
        return ListingPrefilterConfig.model_validate(value)
    except ValidationError:
        logging.getLogger(__name__).warning("prefilter.config_invalid field=prefilter error=ValidationError")
        return None
