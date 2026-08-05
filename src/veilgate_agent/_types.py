from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Literal


@dataclass(frozen=True)
class DetectedSpan:
    label: str
    start: int
    end: int
    text: str
    placeholder: str = ""


@dataclass(frozen=True)
class RedactionResult:
    schema_version: int
    summary: dict[str, object]
    text: str
    detected_spans: tuple[DetectedSpan, ...]
    redacted_text: str
    warning: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": int(self.schema_version),
            "summary": dict(self.summary),
            "text": self.text,
            "detected_spans": [
                {
                    "label": span.label,
                    "start": span.start,
                    "end": span.end,
                    "text": span.text,
                    "placeholder": span.placeholder,
                }
                for span in self.detected_spans
            ],
            "redacted_text": self.redacted_text,
        }
        if self.warning is not None:
            payload["warning"] = self.warning
        return payload

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass(frozen=True)
class MaskMapping:
    placeholder: str
    original: str
    label: str


@dataclass(frozen=True)
class MaskResult:
    text: str
    mappings: tuple[MaskMapping, ...]
    detected_spans: tuple[DetectedSpan, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "mappings": [
                {
                    "placeholder": item.placeholder,
                    "original": item.original,
                    "label": item.label,
                }
                for item in self.mappings
            ],
            "detected_spans": [
                {
                    "label": span.label,
                    "start": span.start,
                    "end": span.end,
                    "text": span.text,
                    "placeholder": span.placeholder,
                }
                for span in self.detected_spans
            ],
        }


class _InheritType:
    def __repr__(self) -> str:
        return "INHERIT"


INHERIT = _InheritType()


@dataclass(frozen=True)
class DecodeOptions:
    decode_mode: Literal["viterbi", "argmax"] | _InheritType = INHERIT
    viterbi_calibration_path: str | None | _InheritType = INHERIT

