from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import os
import re
from typing import Iterable

from ._candidate_classifier import classify_candidate
from ._detector import detect as detect_rules
from ._nym import NymRuntime
from ._settings import load_settings
from ._policy import detect_blockers
NYM_TO_LABEL = {
    "person": "private_person",
    "address": "private_address",
    "company_name": "private_organization",
    "email": "private_email",
    "phone": "private_phone",
    "username": "account_number",
    "medical_record_number": "account_number",
    "date_of_birth": "private_date",
    "government_id": "account_number",
    "credit_debit_card": "account_number",
    "api_key": "secret",
    "password": "secret",
    "url": "private_url",
}


@dataclass(frozen=True)
class HybridFinding:
    label: str
    start: int
    end: int
    text: str
    source: str
    confidence: float | None = None
    entity_type: str | None = None
    placeholder: str = ""


@dataclass(frozen=True)
class HybridMapping:
    label: str
    original: str
    placeholder: str
    source: str
    confidence: float | None = None
    entity_type: str | None = None


@dataclass(frozen=True)
class HybridMaskResult:
    text: str
    mappings: tuple[HybridMapping, ...]
    findings: tuple[HybridFinding, ...]


class HybridMasker:
    def __init__(
        self,
        *,
        enable_nym: bool = True,
        enabled_rules: Iterable[str] | None = None,
    ) -> None:
        self._counters: Counter[str] = Counter()
        self._tokens: dict[tuple[str, str], str] = {}
        settings = load_settings()
        self.enabled_rules = frozenset(
            settings.enabled_rules if enabled_rules is None else enabled_rules
        )
        self.credential_action = settings.credential_action
        self.nym: NymRuntime | None = None
        self.nym_error: str | None = None
        if enable_nym and os.environ.get("ANXINAI_DISABLE_NYM") != "1":
            try:
                self.nym = NymRuntime()
            except Exception as error:
                self.nym_error = str(error)
        elif not enable_nym:
            self.nym_error = "Nym 已由调用方禁用。"

    @property
    def engine_status(self) -> dict[str, object]:
        return {
            "rules": True,
            "nym": self.nym is not None,
            "nym_error": self.nym_error,
            "nym_model": (
                str(self.nym.model_directory) if self.nym is not None else None
            ),
            "enabled_rules": sorted(self.enabled_rules),
            "enabled_rule_count": len(self.enabled_rules),
            "credential_action": self.credential_action,
        }

    def _nym_rule_enabled(self, entity_type: str) -> bool:
        return {
            "person": "person_name",
            "address": "address",
            "company_name": "organization_name",
        }.get(entity_type) in self.enabled_rules

    def discover_semantic_terms(self, text: str) -> tuple[HybridFinding, ...]:
        if self.nym is None:
            return ()
        terms: dict[tuple[str, str], HybridFinding] = {}
        for entity in self.nym.detect(text):
            if (
                entity.type not in {"person", "address", "company_name"}
                or not self._nym_rule_enabled(entity.type)
            ):
                continue
            value = entity.value.strip()
            if not value:
                continue
            if not classify_candidate(
                entity.type,
                value,
                text=text,
                start=entity.start,
                confidence=entity.confidence,
                source="nym",
            ).accepted:
                continue
            label = NYM_TO_LABEL[entity.type]
            terms[(label, value)] = HybridFinding(
                label=label,
                start=0,
                end=len(value),
                text=value,
                source="nym",
                confidence=entity.confidence,
                entity_type=entity.type,
            )
        return tuple(terms.values())

    def _findings(
        self,
        text: str,
        semantic_terms: Iterable[HybridFinding],
        *,
        use_nym: bool,
    ) -> list[HybridFinding]:
        rule_entity_types = {
            "private_person": "person",
            "private_organization": "company_name",
            "private_address": "address",
        }
        rules: list[HybridFinding] = []
        if self.credential_action == "tokenize":
            for finding in detect_blockers(text):
                if finding.kind == "credential":
                    rules.append(HybridFinding(
                        label="secret",
                        start=finding.start,
                        end=finding.end,
                        text=text[finding.start:finding.end],
                        source="policy",
                        confidence=1.0,
                        entity_type="credential",
                    ))
        for span in detect_rules(text, enabled_rules=self.enabled_rules):
            entity_type = rule_entity_types.get(span.label)
            if entity_type and not classify_candidate(
                entity_type,
                span.text,
                text=text,
                start=span.start,
                confidence=1.0,
                source="rule",
            ).accepted:
                continue
            rules.append(HybridFinding(
                label=span.label,
                start=span.start,
                end=span.end,
                text=span.text,
                source="rule",
                confidence=1.0,
                entity_type=entity_type,
            ))
        semantic: list[HybridFinding] = []
        for term in semantic_terms:
            for match in re.finditer(re.escape(term.text), text):
                if not classify_candidate(
                    term.entity_type or "",
                    term.text,
                    text=text,
                    start=match.start(),
                    confidence=term.confidence,
                    source=term.source,
                ).accepted:
                    continue
                semantic.append(
                    HybridFinding(
                        **{
                            **term.__dict__,
                            "start": match.start(),
                            "end": match.end(),
                        }
                    )
                )
        if use_nym and self.nym is not None:
            for entity in self.nym.detect(text):
                if (
                    entity.type not in {"person", "address", "company_name"}
                    or not self._nym_rule_enabled(entity.type)
                ):
                    continue
                label = NYM_TO_LABEL.get(entity.type)
                if label is None:
                    continue
                if not classify_candidate(
                    entity.type,
                    entity.value,
                    text=text,
                    start=entity.start,
                    confidence=entity.confidence,
                    source="nym",
                ).accepted:
                    continue
                semantic.append(
                    HybridFinding(
                        label=label,
                        start=entity.start,
                        end=entity.end,
                        text=entity.value,
                        source="nym",
                        confidence=entity.confidence,
                        entity_type=entity.type,
                    )
                )
        accepted = list(rules)
        for candidate in sorted(
            semantic,
            key=lambda item: (
                item.start,
                -(item.end - item.start),
                -(item.confidence or 0),
            ),
        ):
            if any(
                candidate.start < current.end and candidate.end > current.start
                for current in accepted
            ):
                continue
            accepted.append(candidate)
        return sorted(accepted, key=lambda item: item.start)

    def mask(
        self,
        text: str,
        *,
        semantic_terms: Iterable[HybridFinding] = (),
        use_nym: bool = True,
    ) -> HybridMaskResult:
        findings = self._findings(
            text,
            semantic_terms,
            use_nym=use_nym,
        )
        pieces: list[str] = []
        cursor = 0
        mappings: list[HybridMapping] = []
        masked_findings: list[HybridFinding] = []
        for finding in findings:
            key = (finding.label, finding.text)
            placeholder = self._tokens.get(key)
            if placeholder is None:
                self._counters[finding.label] += 1
                placeholder = (
                    f"[VG_{finding.label.upper()}_"
                    f"{self._counters[finding.label]}]"
                )
                self._tokens[key] = placeholder
            pieces.extend((text[cursor : finding.start], placeholder))
            cursor = finding.end
            mapping = HybridMapping(
                label=finding.label,
                original=finding.text,
                placeholder=placeholder,
                source=finding.source,
                confidence=finding.confidence,
                entity_type=finding.entity_type,
            )
            mappings.append(mapping)
            masked_findings.append(
                HybridFinding(
                    **{**finding.__dict__, "placeholder": placeholder}
                )
            )
        pieces.append(text[cursor:])
        return HybridMaskResult(
            text="".join(pieces),
            mappings=tuple(mappings),
            findings=tuple(masked_findings),
        )
