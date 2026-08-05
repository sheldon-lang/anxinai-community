from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class BlockedFinding:
    kind: str
    label: str
    start: int
    end: int


_BLOCKERS = (
    (
        "credential",
        "密码、Token 或 API Key",
        re.compile(
            r"(?:密码|口令|访问令牌|Access[\s_-]*Token|API[\s_-]*Key|Secret)"
            r"[：:\s=]*([^，。；;\s]{4,100})",
            re.I,
        ),
    ),
    (
        "classified",
        "密级或禁止外传标识",
        re.compile(r"(?:密级[：:\s]*(?:绝密|机密|秘密)|内部资料|未经授权不得外传)"),
    ),
    (
        "unpublished",
        "未发布材料",
        re.compile(
            r"(?:稿件状态|发布状态)[：:\s]*(?:未发布|待发布|暂缓发布)"
        ),
    ),
)


def detect_blockers(text: str) -> tuple[BlockedFinding, ...]:
    findings: list[BlockedFinding] = []
    for kind, label, pattern in _BLOCKERS:
        for match in pattern.finditer(text):
            start, end = (
                match.span(1)
                if kind == "credential" and match.lastindex
                else match.span()
            )
            findings.append(
                BlockedFinding(
                    kind=kind,
                    label=label,
                    start=start,
                    end=end,
                )
            )
    return tuple(sorted(findings, key=lambda item: item.start))


def blocker_message(text: str, *, credential_action: str = "block") -> str | None:
    findings = tuple(
        finding for finding in detect_blockers(text)
        if finding.kind != "credential" or credential_action == "block"
    )
    if not findings:
        return None
    if all(item.kind == "credential" for item in findings):
        return (
            "检测到密码、Token 或 API Key，默认已阻断。"
            "可在保护设置中改为“脱敏并可恢复”后重新处理。"
        )
    labels = "、".join(dict.fromkeys(item.label for item in findings))
    return f"检测到不可关闭的固定安全风险（{labels}），已停止处理。"
