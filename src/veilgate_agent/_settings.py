from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class RuleGroup:
    id: str
    label: str
    description: str
    rules: tuple[RuleDefinition, ...]


RULE_GROUPS = (
    RuleGroup(
        "identity",
        "个人身份与联系",
        "自然人的身份号码、联系方式和联系地址。",
        (
            RuleDefinition("person_name", "姓名", "字段上下文与 Nym 本地模型"),
            RuleDefinition("cn_id", "身份证号码", "校验 18 位身份证校验码"),
            RuleDefinition("mobile", "手机号码", "中国大陆 11 位手机号"),
            RuleDefinition("phone", "固定电话", "带区号的办公或家庭电话"),
            RuleDefinition("email", "电子邮箱", "个人及企业邮箱地址"),
            RuleDefinition("address", "联系地址", "地址字段与 Nym 本地模型"),
        ),
    ),
    RuleGroup(
        "organization",
        "单位与业务",
        "单位主体信息以及合同、项目和采购编号。",
        (
            RuleDefinition("organization_name", "单位名称", "字段上下文与 Nym 本地模型"),
            RuleDefinition("organization_date", "成立或注册日期", "带明确字段名称的日期"),
            RuleDefinition("social_credit_code", "统一社会信用代码", "格式与校验位验证"),
            RuleDefinition("contract_code", "合同编号", "合同编号、合同号字段"),
            RuleDefinition("project_code", "项目编号", "项目编号、项目代号字段"),
            RuleDefinition(
                "procurement_code",
                "采购与招投标编号",
                "采购单号、采购编号、招投标编号",
            ),
        ),
    ),
    RuleGroup(
        "hr",
        "人力资源",
        "员工标识、薪酬和人事结果类字段。",
        (
            RuleDefinition("employee_id", "员工编号", "员工编号、员工号、工号"),
            RuleDefinition("salary_amount", "工资与薪酬", "月薪、工资、薪酬字段金额"),
            RuleDefinition("bonus_amount", "奖金金额", "奖金字段金额"),
            RuleDefinition("performance_result", "绩效结果", "绩效结果字段"),
            RuleDefinition("assessment_result", "考核结果", "考核结果字段"),
            RuleDefinition("leave_reason", "请假原因", "请假原因字段"),
            RuleDefinition("disciplinary_result", "奖惩结果", "奖惩结果字段"),
        ),
    ),
    RuleGroup(
        "finance",
        "财务采购",
        "银行卡、预算、发票与收付款账号。",
        (
            RuleDefinition("bank_card", "银行卡号", "通过 Luhn 校验后替换"),
            RuleDefinition("budget_amount", "预算金额", "预算字段金额"),
            RuleDefinition("invoice_number", "发票号码", "发票号码、发票代码字段"),
            RuleDefinition("payment_account", "收付款账号", "付款、收款或银行账号字段"),
        ),
    ),
    RuleGroup(
        "party",
        "党建信息",
        "带明确字段名称的党员与组织关系信息。",
        (
            RuleDefinition("party_member_id", "党员编号", "党员编号、党员号"),
            RuleDefinition("party_fee", "党费金额", "党费金额字段"),
            RuleDefinition("democratic_review", "民主评议结果", "民主评议结果字段"),
            RuleDefinition("organization_relation", "组织关系状态", "组织关系状态字段"),
        ),
    ),
    RuleGroup(
        "system",
        "办公与系统",
        "办公网络中可明确判断的内部地址。",
        (
            RuleDefinition("intranet", "内网 IP 地址", "10/172.16–31/192.168 私有地址"),
        ),
    ),
)

ALL_RULE_IDS = tuple(
    rule.id for group in RULE_GROUPS for rule in group.rules
)
VALID_RULE_IDS = frozenset(ALL_RULE_IDS)

FIXED_BLOCKERS = (
    ("classified", "密级与禁止外传标识", "绝密、机密、秘密、内部资料等"),
    ("unpublished", "未发布材料", "未发布、待发布、暂缓发布"),
    ("unscannable", "无法完整扫描的文件", "损坏、超限、加密或不支持结构"),
    ("media", "图片、扫描件与压缩包", "当前版本无法可靠读取全部内容"),
    ("integrity", "文件完整性异常", "类型伪装、结构异常或结果不可信"),
)


@dataclass(frozen=True)
class AgentSettings:
    enabled_rules: tuple[str, ...] = ALL_RULE_IDS
    credential_action: str = "block"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "enabled_rules": list(self.enabled_rules),
            "credential_action": self.credential_action,
        }


def anxinai_home() -> Path:
    configured = os.environ.get("ANXINAI_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".anxinai"


def settings_path() -> Path:
    return anxinai_home() / "settings.json"


def normalize_enabled_rules(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ALL_RULE_IDS
    selected = {
        item for item in value
        if isinstance(item, str) and item in VALID_RULE_IDS
    }
    return tuple(rule_id for rule_id in ALL_RULE_IDS if rule_id in selected)


def load_settings() -> AgentSettings:
    path = settings_path()
    if not path.is_file():
        return AgentSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AgentSettings()
    if not isinstance(payload, dict):
        return AgentSettings()
    return AgentSettings(
        enabled_rules=normalize_enabled_rules(payload.get("enabled_rules")),
        credential_action=(
            "tokenize" if payload.get("credential_action") == "tokenize" else "block"
        ),
    )


def save_settings(
    enabled_rules: Iterable[str],
    *,
    credential_action: str = "block",
) -> AgentSettings:
    selected = set(enabled_rules)
    settings = AgentSettings(
        enabled_rules=tuple(
            rule_id for rule_id in ALL_RULE_IDS if rule_id in selected
        ),
        credential_action="tokenize" if credential_action == "tokenize" else "block",
    )
    path = settings_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return settings
