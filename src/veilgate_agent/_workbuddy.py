from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Any
import zipfile

from ._documents import (
    DocumentProcessingError,
    SUPPORTED_SUFFIXES,
    TEXT_SUFFIXES,
    process_document,
)

LABEL_NAMES = {
    "private_person": "姓名",
    "private_phone": "手机号/电话",
    "private_email": "邮箱",
    "private_address": "地址",
    "private_organization": "单位名称",
    "private_url": "网址",
    "private_date": "日期",
    "account_number": "证件/账号",
    "secret": "密钥/密码",
    "organization_date": "成立/注册日期",
    "social_credit_code": "统一社会信用代码",
    "business_code": "业务编号",
    "employee_id": "员工编号",
    "salary_amount": "工资/薪酬",
    "bonus_amount": "奖金",
    "performance_result": "绩效结果",
    "assessment_result": "考核结果",
    "leave_reason": "请假原因",
    "disciplinary_result": "奖惩结果",
    "budget_amount": "预算金额",
    "invoice_number": "发票号码",
    "payment_account": "收付款账号",
    "party_member_id": "党员编号",
    "party_fee": "党费金额",
    "democratic_review": "民主评议",
    "organization_relation": "组织关系",
    "intranet": "内网 IP",
    "redacted": "敏感信息",
}


class WorkBuddyReviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewSummary:
    ticket_id: str
    status: str
    source_name: str
    counts: dict[str, int]
    finding_count: int
    engines: dict[str, bool]
    output_format: str
    warnings: tuple[str, ...] = ()
    safe_path: str | None = None
    message: str | None = None
    restore_required: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "ticket_id": self.ticket_id,
            "status": self.status,
            "source_name": self.source_name,
            "counts": dict(self.counts),
            "finding_count": self.finding_count,
            "engines": dict(self.engines),
            "output_format": self.output_format,
        }
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        if self.safe_path is not None:
            payload["safe_path"] = self.safe_path
        if self.message is not None:
            payload["message"] = self.message
        if self.restore_required:
            payload["restore_required"] = True
            payload["completion_requirement"] = (
                "基础模式任务尚未完成：处理安全版后，必须把占位符回复写入"
                "临时 UTF-8 文件并调用 anxin-gate restore；不得只在聊天中"
                "提示用户可以恢复。"
            )
        return payload


def anxinai_home() -> Path:
    configured = os.environ.get("ANXINAI_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".anxinai"


def review_directory() -> Path:
    path = anxinai_home() / "workbuddy" / "reviews"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def restore_directory() -> Path:
    path = anxinai_home() / "workbuddy" / "restores"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def ticket_path(ticket_id: str) -> Path:
    if not ticket_id or any(character not in "0123456789abcdef" for character in ticket_id):
        raise WorkBuddyReviewError("无效的审核编号。")
    return review_directory() / f"{ticket_id}.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_ticket(payload: dict[str, Any]) -> None:
    path = ticket_path(str(payload["ticket_id"]))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def load_ticket(ticket_id: str) -> dict[str, Any]:
    path = ticket_path(ticket_id)
    if not path.is_file():
        raise WorkBuddyReviewError("未找到本地审核记录。")
    return json.loads(path.read_text(encoding="utf-8"))


def update_ticket(ticket_id: str, **updates: Any) -> dict[str, Any]:
    payload = load_ticket(ticket_id)
    payload.update(updates)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    _write_ticket(payload)
    return payload


def _safe_output_path(
    source: Path,
    ticket_id: str,
    *,
    output_suffix: str | None = None,
) -> Path:
    suffix = output_suffix or source.suffix
    for length in (12, 20, 32):
        candidate = source.with_name(f"anxinai-safe-{ticket_id[:length]}{suffix}")
        try:
            descriptor = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate
    raise WorkBuddyReviewError("无法创建唯一的安全版文件，请清理同目录旧文件后重试。")


def create_review(source_path: str | os.PathLike[str]) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise WorkBuddyReviewError("指定路径不是可读取的文件。")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise WorkBuddyReviewError(
            f"社区版暂不支持 {source.suffix or '无扩展名'} 文件。"
        )
    ticket_id = secrets.token_hex(16)
    access_token = secrets.token_urlsafe(24)
    safe_path = _safe_output_path(
        source,
        ticket_id,
        output_suffix=".md" if source.suffix.lower() == ".pdf" else None,
    )
    try:
        processed = process_document(source, safe_path)
    except DocumentProcessingError as error:
        if safe_path.is_file() and not safe_path.is_symlink():
            safe_path.unlink()
        raise WorkBuddyReviewError(str(error)) from error
    safe_path = processed.safe_path

    counts = Counter(mapping.label for mapping in processed.mappings)
    warnings = list(processed.warnings)
    if processed.engine_status.get("nym_error"):
        warnings.append(f"Nym 未启用：{processed.engine_status['nym_error']}")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "ticket_id": ticket_id,
        "access_token": access_token,
        "status": "pending",
        "source_path": str(source),
        "source_name": source.name,
        "source_sha256": _sha256(source),
        "safe_path": str(safe_path),
        "safe_sha256": _sha256(safe_path),
        "counts": dict(counts),
        "finding_count": len(processed.mappings),
        "engine_status": processed.engine_status,
        "output_format": processed.output_format,
        "warnings": warnings,
        "mappings": [
            {
                "label": mapping.label,
                "label_name": LABEL_NAMES.get(mapping.label, mapping.label),
                "original": mapping.original,
                "placeholder": mapping.placeholder,
                "source": mapping.source,
                "confidence": mapping.confidence,
                "entity_type": mapping.entity_type,
            }
            for mapping in processed.mappings
        ],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "review_url": None,
    }
    _write_ticket(payload)
    return payload


def validate_ticket_files(payload: dict[str, Any]) -> tuple[bool, str | None]:
    source = Path(str(payload["source_path"]))
    safe = Path(str(payload["safe_path"]))
    if not source.is_file():
        return False, "原文件已不存在，本次审核失效。"
    if _sha256(source) != payload.get("source_sha256"):
        return False, "原文件在审核期间发生变化，请重新检查。"
    if not safe.is_file() or _sha256(safe) != payload.get("safe_sha256"):
        return False, "安全版文件已变化，请重新检查。"
    return True, None


def decide_review(
    ticket_id: str,
    access_token: str,
    decision: str,
) -> dict[str, Any]:
    payload = load_ticket(ticket_id)
    if not secrets.compare_digest(
        str(payload.get("access_token", "")),
        access_token,
    ):
        raise WorkBuddyReviewError("审核链接无效或已经失效。")
    if payload.get("status") != "pending":
        return payload
    if decision == "approved":
        valid, message = validate_ticket_files(payload)
        if not valid:
            return update_ticket(
                ticket_id,
                status="invalid",
                message=message,
                mappings=[],
            )
        return update_ticket(
            ticket_id,
            status="approved",
            approved_at=datetime.now(UTC).isoformat(),
            message="用户已在本地审核页确认使用安全版。",
            mapping_status="available_for_local_restore",
        )
    if decision == "cancelled":
        safe_path = Path(str(payload["safe_path"]))
        if safe_path.is_file():
            safe_path.unlink()
        return update_ticket(
            ticket_id,
            status="cancelled",
            message="用户已取消本次文件读取。",
            mappings=[],
        )
    raise WorkBuddyReviewError("不支持的审核决定。")


def restore_session_path(session_id: str) -> Path:
    if not session_id or any(
        character not in "0123456789abcdef" for character in session_id
    ):
        raise WorkBuddyReviewError("无效的本地恢复编号。")
    return restore_directory() / f"{session_id}.json"


def load_restore_session(session_id: str) -> dict[str, Any]:
    path = restore_session_path(session_id)
    if not path.is_file():
        raise WorkBuddyReviewError("本地恢复结果已失效。")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkBuddyReviewError("本地恢复结果损坏。") from error


def update_restore_session(session_id: str, **updates: Any) -> dict[str, Any]:
    payload = load_restore_session(session_id)
    payload.update(updates)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    path = restore_session_path(session_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return payload


def delete_restore_session(session_id: str) -> None:
    path = restore_session_path(session_id)
    if path.is_file() and not path.is_symlink():
        path.unlink()


def _restore_mapped_text(text: str, mappings: list[dict[str, Any]]) -> str:
    restored = text
    for item in sorted(
        mappings,
        key=lambda mapping: len(str(mapping.get("placeholder", ""))),
        reverse=True,
    ):
        placeholder = str(item.get("placeholder", ""))
        if placeholder:
            restored = restored.replace(
                placeholder,
                str(item.get("original", "")),
            )
    return restored


def _validated_restore_ticket(ticket_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ticket = load_ticket(ticket_id)
    if ticket.get("status") != "approved":
        raise WorkBuddyReviewError("只有已确认的安全版任务可以恢复结果。")
    mappings = list(ticket.get("mappings", []))
    if not mappings:
        raise WorkBuddyReviewError("本次映射已使用或已销毁，无法再次恢复。")
    return ticket, mappings


def _new_restore_payload(
    ticket: dict[str, Any],
    *,
    result_kind: str,
    restored_text: str = "",
    restored_file_path: str = "",
) -> dict[str, Any]:
    session_id = secrets.token_hex(16)
    access_token = secrets.token_urlsafe(24)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "session_id": session_id,
        "ticket_id": str(ticket["ticket_id"]),
        "access_token": access_token,
        "status": "pending",
        "result_kind": result_kind,
        "source_name": str(ticket.get("source_name", "")),
        "restored_text": restored_text,
        "restored_file_path": restored_file_path,
        "restored_file_name": (
            Path(restored_file_path).name if restored_file_path else ""
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "restore_url": None,
    }
    path = restore_session_path(session_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    update_ticket(
        str(ticket["ticket_id"]),
        mappings=[],
        mapping_status="consumed_by_local_restore",
        restored_at=datetime.now(UTC).isoformat(),
        restored_result_kind=result_kind,
    )
    return payload


def create_restore_session(
    ticket_id: str,
    safe_response_path: str | os.PathLike[str],
) -> dict[str, Any]:
    ticket, mappings = _validated_restore_ticket(ticket_id)

    response_path = Path(safe_response_path).expanduser().resolve()
    if not response_path.is_file() or response_path.is_symlink():
        raise WorkBuddyReviewError("未找到 Agent 生成的安全回复文件。")
    if response_path.stat().st_size > 2 * 1024 * 1024:
        raise WorkBuddyReviewError("安全回复超过 2 MB，请拆分后恢复。")
    try:
        safe_text = response_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise WorkBuddyReviewError("安全回复必须是 UTF-8 文本。") from error

    return _new_restore_payload(
        ticket,
        result_kind="text",
        restored_text=_restore_mapped_text(safe_text, mappings),
    )


def _restored_output_path(source: Path, session_id: str) -> Path:
    for length in (12, 20, 32):
        candidate = source.with_name(
            f"anxinai-restored-{session_id[:length]}{source.suffix.lower()}"
        )
        try:
            descriptor = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate
    raise WorkBuddyReviewError("无法创建唯一的本地恢复文件。")


def _restore_complete_file(
    source: Path,
    target: Path,
    mappings: list[dict[str, Any]],
) -> None:
    suffix = source.suffix.lower()
    if source.stat().st_size > 64 * 1024 * 1024:
        raise WorkBuddyReviewError("待恢复文件超过 64 MB，请拆分后重试。")
    try:
        if suffix in TEXT_SUFFIXES:
            restored = _restore_mapped_text(
                source.read_text(encoding="utf-8"),
                mappings,
            )
            target.write_text(restored, encoding="utf-8")
            os.chmod(target, 0o600)
            return
        if suffix not in {".docx", ".xlsx"}:
            raise WorkBuddyReviewError(
                "完整文件恢复当前支持 UTF-8 文本、DOCX 和 XLSX；"
                "PDF 请让 Agent 先输出安全版 DOCX 或 Markdown。"
            )
        with zipfile.ZipFile(source) as reader:
            infos = reader.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise WorkBuddyReviewError("待恢复 Office 文件包含重复内部路径。")
            if sum(info.file_size for info in infos) > 128 * 1024 * 1024:
                raise WorkBuddyReviewError("待恢复 Office 文件解压后超过 128 MB。")
            with zipfile.ZipFile(target, "w") as writer:
                for info in infos:
                    data = reader.read(info)
                    if (
                        info.filename.endswith((".xml", ".rels"))
                        or info.filename == "[Content_Types].xml"
                    ):
                        text = data.decode("utf-8")
                        data = _restore_mapped_text(text, mappings).encode("utf-8")
                    writer.writestr(info, data)
        os.chmod(target, 0o600)
    except WorkBuddyReviewError:
        if target.is_file():
            target.unlink()
        raise
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError) as error:
        if target.is_file():
            target.unlink()
        raise WorkBuddyReviewError("完整文件本地恢复失败。") from error


def create_restore_file_session(
    ticket_id: str,
    safe_output_path: str | os.PathLike[str],
) -> dict[str, Any]:
    ticket, mappings = _validated_restore_ticket(ticket_id)
    source = Path(safe_output_path).expanduser().resolve()
    if not source.is_file() or source.is_symlink():
        raise WorkBuddyReviewError("未找到 Agent 生成的安全输出文件。")
    session_id = secrets.token_hex(16)
    target = _restored_output_path(source, session_id)
    _restore_complete_file(source, target, mappings)
    return _new_restore_payload(
        ticket,
        result_kind="file",
        restored_file_path=str(target),
    )


def expire_review(ticket_id: str, access_token: str) -> dict[str, Any]:
    payload = load_ticket(ticket_id)
    if not secrets.compare_digest(
        str(payload.get("access_token", "")),
        access_token,
    ):
        raise WorkBuddyReviewError("审核链接无效或已经失效。")
    if payload.get("status") != "pending":
        return payload
    safe_path = Path(str(payload["safe_path"]))
    if safe_path.is_file():
        safe_path.unlink()
    return update_ticket(
        ticket_id,
        status="expired",
        message="本地审核已超时，请重新检查文件。",
        mappings=[],
    )


def public_summary(payload: dict[str, Any]) -> ReviewSummary:
    status = str(payload.get("status", "invalid"))
    safe_path = str(payload["safe_path"]) if status == "approved" else None
    counts = {
        LABEL_NAMES.get(label, label): int(count)
        for label, count in dict(payload.get("counts", {})).items()
    }
    engine_status = dict(payload.get("engine_status", {}))
    return ReviewSummary(
        ticket_id=str(payload["ticket_id"]),
        status=status,
        source_name=str(payload.get("source_name", "")),
        counts=counts,
        finding_count=int(payload.get("finding_count", 0)),
        engines={
            "rules": bool(engine_status.get("rules", True)),
            "nym": bool(engine_status.get("nym", False)),
        },
        output_format=str(payload.get("output_format", "")),
        warnings=tuple(str(item) for item in payload.get("warnings", [])),
        safe_path=safe_path,
        message=str(payload["message"]) if payload.get("message") else None,
        restore_required=(
            status == "approved"
            and bool(payload.get("mappings"))
            and payload.get("mapping_status")
            != "consumed_by_local_restore"
        ),
    )
