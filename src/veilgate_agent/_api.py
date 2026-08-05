from __future__ import annotations

from collections import Counter
import functools
import os
from pathlib import Path
from typing import Literal

from ._detector import detect
from ._types import (
    DecodeOptions,
    DetectedSpan,
    INHERIT,
    MaskMapping,
    MaskResult,
    RedactionResult,
)


_OUTPUT_MODES = frozenset({"typed", "redacted"})
_DECODE_MODES = frozenset({"viterbi", "argmax"})


def _replace(text: str, spans: tuple[DetectedSpan, ...]) -> str:
    pieces: list[str] = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor:span.start])
        pieces.append(span.placeholder)
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces)


class OPF:
    """OPF-compatible local redactor backed by AnxinAI Chinese PII rules."""

    def __init__(
        self,
        *,
        model: str | os.PathLike[str] | None = None,
        context_window_length: int | None = None,
        trim_whitespace: bool = True,
        device: Literal["cpu", "cuda"] = "cuda",
        output_mode: Literal["typed", "redacted"] = "typed",
        decode_mode: Literal["viterbi", "argmax"] = "viterbi",
        discard_overlapping_predicted_spans: bool = False,
        output_text_only: bool = False,
    ) -> None:
        if output_mode not in _OUTPUT_MODES:
            raise ValueError(f"Unsupported output_mode: {output_mode!r}")
        if decode_mode not in _DECODE_MODES:
            raise ValueError(f"Unsupported decode_mode: {decode_mode!r}")
        self._checkpoint = str(Path(model).expanduser()) if model is not None else None
        self._context_window_length = context_window_length
        self._trim_whitespace = bool(trim_whitespace)
        self._device = str(device)
        self._output_mode = str(output_mode)
        self._decode_mode = str(decode_mode)
        self._viterbi_calibration_path: str | None = None
        self._discard_overlapping_predicted_spans = bool(discard_overlapping_predicted_spans)
        self._output_text_only = bool(output_text_only)

    def redact(
        self,
        text: str,
        *,
        decode: DecodeOptions | None = None,
    ) -> str | RedactionResult:
        self.resolve_decode_options(decode=decode)
        raw_spans = detect(text, trim_whitespace=self._trim_whitespace)
        spans = tuple(
            DetectedSpan(
                label="redacted" if self._output_mode == "redacted" else span.label,
                start=span.start,
                end=span.end,
                text=span.text,
                placeholder="",
            )
            for span in raw_spans
        )
        redacted_text = _replace(text, spans)
        if self._output_text_only:
            return redacted_text
        counts = Counter(span.label for span in spans)
        return RedactionResult(
            schema_version=1,
            summary={
                "output_mode": self._output_mode,
                "span_count": len(spans),
                "by_label": dict(counts),
                "decoded_mismatch": False,
            },
            text=text,
            detected_spans=spans,
            redacted_text=redacted_text,
        )

    def mask(self, text: str) -> MaskResult:
        """AnxinAI extension: replace PII with stable local placeholders."""
        raw_spans = detect(text, trim_whitespace=self._trim_whitespace)
        counters: Counter[str] = Counter()
        spans: list[DetectedSpan] = []
        mappings: list[MaskMapping] = []
        for span in raw_spans:
            counters[span.label] += 1
            placeholder = f"[VG_{span.label.upper()}_{counters[span.label]}]"
            spans.append(
                DetectedSpan(
                    label=span.label,
                    start=span.start,
                    end=span.end,
                    text=span.text,
                    placeholder=placeholder,
                )
            )
            mappings.append(
                MaskMapping(
                    placeholder=placeholder,
                    original=span.text,
                    label=span.label,
                )
            )
        span_tuple = tuple(spans)
        return MaskResult(
            text=_replace(text, span_tuple),
            mappings=tuple(mappings),
            detected_spans=span_tuple,
        )

    def restore(
        self,
        text: str,
        mappings: MaskResult | tuple[MaskMapping, ...] | list[MaskMapping],
    ) -> str:
        """AnxinAI extension: restore placeholders using an in-memory mapping."""
        items = mappings.mappings if isinstance(mappings, MaskResult) else mappings
        restored = text
        for item in sorted(items, key=lambda value: len(value.placeholder), reverse=True):
            restored = restored.replace(item.placeholder, item.original)
        return restored

    def set_model_path(self, model_path: str | os.PathLike[str]) -> OPF:
        self._checkpoint = str(Path(model_path).expanduser())
        return self

    def set_device(self, *, device: Literal["cpu", "cuda"]) -> OPF:
        self._device = str(device)
        return self

    def set_output_mode(self, output_mode: Literal["typed", "redacted"]) -> OPF:
        if output_mode not in _OUTPUT_MODES:
            raise ValueError(f"Unsupported output_mode: {output_mode!r}")
        self._output_mode = str(output_mode)
        return self

    def set_decode_mode(self, decode_mode: Literal["viterbi", "argmax"]) -> OPF:
        if decode_mode not in _DECODE_MODES:
            raise ValueError(f"Unsupported decode_mode: {decode_mode!r}")
        self._decode_mode = str(decode_mode)
        return self

    def set_viterbi_decoder(
        self,
        *,
        calibration_path: str | os.PathLike[str] | None = None,
    ) -> OPF:
        self._decode_mode = "viterbi"
        self._viterbi_calibration_path = (
            str(Path(calibration_path).expanduser())
            if calibration_path is not None
            else None
        )
        return self

    def trim_whitespace(self, trim: bool = True) -> OPF:
        self._trim_whitespace = bool(trim)
        return self

    def output_text_only(self, text_only: bool = True) -> OPF:
        self._output_text_only = bool(text_only)
        return self

    def resolve_decode_options(
        self,
        *,
        decode: DecodeOptions | None = None,
    ) -> DecodeOptions:
        options = decode or DecodeOptions()
        decode_mode = self._decode_mode if options.decode_mode is INHERIT else options.decode_mode
        calibration = (
            self._viterbi_calibration_path
            if options.viterbi_calibration_path is INHERIT
            else options.viterbi_calibration_path
        )
        if decode_mode not in _DECODE_MODES:
            raise ValueError(f"Unsupported decode_mode: {decode_mode!r}")
        return DecodeOptions(
            decode_mode=decode_mode,
            viterbi_calibration_path=calibration,
        )


@functools.lru_cache(maxsize=1)
def _default_redactor() -> OPF:
    return OPF(output_text_only=True)


def redact(text: str) -> str:
    return str(_default_redactor().redact(text))
