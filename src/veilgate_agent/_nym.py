from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator


class NymUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class NymEntity:
    type: str
    label: str
    start: int
    end: int
    value: str
    confidence: float


ENTITY_LABELS = {
    "person": "姓名",
    "address": "地址",
    "company_name": "单位名称",
    "email": "邮箱",
    "phone": "手机号/电话",
    "username": "用户名",
    "medical_record_number": "病历号",
    "date_of_birth": "出生日期",
    "government_id": "证件号码",
    "credit_debit_card": "银行卡",
    "api_key": "密钥/密码",
    "password": "密钥/密码",
    "url": "网址",
}

ADDRESS_TYPES = frozenset(
    {
        "address",
        "building_number",
        "city",
        "country",
        "secondary_address",
        "state",
        "street_address",
        "street_name",
        "zip_code",
    }
)
PERSON_TYPES = frozenset({"person", "given_name", "surname"})
ENABLED_TYPES = frozenset(
    {
        *ADDRESS_TYPES,
        *PERSON_TYPES,
        "company_name",
        "email",
        "phone",
        "username",
        "medical_record_number",
        "date_of_birth",
        "government_id",
        "credit_debit_card",
        "api_key",
        "password",
        "url",
    }
)


def _normalize_type(raw_type: str) -> str:
    value = raw_type.lower()
    if value in PERSON_TYPES:
        return "person"
    if value in ADDRESS_TYPES:
        return "address"
    return value


def _candidate_model_directories() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("ANXINAI_NYM_MODEL_DIR")
    if configured:
        candidates.append(Path(configured).expanduser())
    anxinai_home = os.environ.get("ANXINAI_HOME")
    if anxinai_home:
        candidates.append(
            Path(anxinai_home).expanduser() / "models" / "nym-edge-int8"
        )
    candidates.append(Path.home() / ".anxinai" / "models" / "nym-edge-int8")
    module = Path(__file__).resolve()
    candidates.append(module.parents[2] / "models" / "nym-edge-int8")
    if len(module.parents) >= 4:
        repository = module.parents[3]
        candidates.extend(
            [
                repository
                / "extension"
                / "releases"
                / "chrome"
                / "0.2.4"
                / "unpacked"
                / "models"
                / "nym-edge-int8",
                repository
                / "extension"
                / "public"
                / "models"
                / "nym-edge-int8",
            ]
        )
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def find_model_directory() -> Path:
    required = ("model.onnx", "tokenizer.json", "config.json")
    for directory in _candidate_model_directories():
        if all((directory / name).is_file() for name in required):
            return directory
    raise NymUnavailableError(
        "未找到 Nym Edge INT8 模型。请运行 anxin-install-model，"
        "或设置 ANXINAI_NYM_MODEL_DIR。"
    )


def _preferred_boundary(text: str, start: int, proposed_end: int) -> int:
    earliest = start + 240
    for index in range(proposed_end - 1, earliest - 1, -1):
        if text[index] in "。！？；\n":
            return index + 1
    return proposed_end


def _chunks(text: str) -> Iterator[tuple[int, str]]:
    start = 0
    while start < len(text):
        proposed_end = min(start + 480, len(text))
        end = (
            proposed_end
            if proposed_end == len(text)
            else _preferred_boundary(text, start, proposed_end)
        )
        yield start, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - 64)


class NymRuntime:
    def __init__(
        self,
        model_directory: Path | None = None,
        *,
        threshold: float = 0.25,
    ) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:
            raise NymUnavailableError(
                "Nym 运行依赖缺失。请安装 onnxruntime、numpy 和 tokenizers。"
            ) from error
        self._np = np
        directory = model_directory or find_model_directory()
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        self._labels = {
            int(index): str(label)
            for index, label in dict(config["id2label"]).items()
        }
        self._tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=512)
        self._session = ort.InferenceSession(
            str(directory / "model.onnx"),
            providers=["CPUExecutionProvider"],
        )
        required = {"input_ids", "attention_mask"}
        input_names = {item.name for item in self._session.get_inputs()}
        if not required.issubset(input_names):
            raise NymUnavailableError("Nym 模型缺少必要输入张量。")
        self.threshold = float(threshold)
        self.model_directory = directory

    def _decode(self, text: str) -> list[NymEntity]:
        encoded = self._tokenizer.encode(text)
        ids = self._np.asarray([encoded.ids], dtype=self._np.int64)
        mask = self._np.asarray([encoded.attention_mask], dtype=self._np.int64)
        outputs = self._session.run(
            None,
            {"input_ids": ids, "attention_mask": mask},
        )
        logits = outputs[0][0]
        best = logits.argmax(axis=-1)
        maximum = logits.max(axis=-1)
        denominators = self._np.exp(logits - maximum[:, None]).sum(axis=-1)
        scores = 1.0 / denominators
        entities: list[NymEntity] = []
        current: dict[str, Any] | None = None

        def flush() -> None:
            nonlocal current
            if current is None:
                return
            value = text[current["start"] : current["end"]]
            if value:
                entities.append(
                    NymEntity(
                        type=current["type"],
                        label=ENTITY_LABELS.get(
                            current["type"], current["raw_type"]
                        ),
                        start=current["start"],
                        end=current["end"],
                        value=value,
                        confidence=current["confidence"],
                    )
                )
            current = None

        for index, offset in enumerate(encoded.offsets):
            label = self._labels.get(int(best[index]), "O")
            score = float(scores[index])
            match = re.fullmatch(r"([BI])-(.+)", label)
            if (
                not match
                or score < self.threshold
                or offset[0] == offset[1]
            ):
                flush()
                continue
            raw_type = match.group(2).lower()
            normalized = _normalize_type(raw_type)
            if normalized not in ENABLED_TYPES and raw_type not in ENABLED_TYPES:
                flush()
                continue
            joins = (
                current is not None
                and current["type"] == normalized
                and offset[0] == current["end"]
                and (
                    normalized == "address"
                    or (
                        current["raw_type"] == "surname"
                        and raw_type == "given_name"
                    )
                )
            )
            continues = joins or (
                match.group(1) == "I"
                and current is not None
                and current["type"] == normalized
                and offset[0] <= current["end"]
            )
            if not continues:
                flush()
                current = {
                    "type": normalized,
                    "raw_type": raw_type,
                    "start": offset[0],
                    "end": offset[1],
                    "confidence": score,
                }
            else:
                current["end"] = max(current["end"], offset[1])
                current["confidence"] = min(current["confidence"], score)
        flush()
        return entities

    def detect(self, text: str) -> tuple[NymEntity, ...]:
        if not text.strip():
            return ()
        shifted: list[NymEntity] = []
        for chunk_start, chunk in _chunks(text):
            for entity in self._decode(chunk):
                shifted.append(
                    NymEntity(
                        **{
                            **entity.__dict__,
                            "start": chunk_start + entity.start,
                            "end": chunk_start + entity.end,
                        }
                    )
                )
        shifted.sort(
            key=lambda item: (
                item.start,
                -item.end,
                -item.confidence,
            )
        )
        merged: list[NymEntity] = []
        for candidate in shifted:
            duplicate = next(
                (
                    index
                    for index, current in enumerate(merged)
                    if current.type == candidate.type
                    and current.start < candidate.end
                    and current.end > candidate.start
                ),
                None,
            )
            if duplicate is None:
                merged.append(candidate)
                continue
            current = merged[duplicate]
            current_length = current.end - current.start
            candidate_length = candidate.end - candidate.start
            if candidate_length > current_length or (
                candidate_length == current_length
                and candidate.confidence > current.confidence
            ):
                merged[duplicate] = candidate
        return tuple(sorted(merged, key=lambda item: item.start))
