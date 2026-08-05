from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
import ipaddress
import re

from ._settings import ALL_RULE_IDS
from ._types import DetectedSpan


_CN = r"\u3400-\u4dbf\u4e00-\u9fff"
_PERSON_HEADER_WORDS = frozenset(
    {
        "日期",
        "星期",
        "时间",
        "手机号",
        "电话",
        "地址",
        "邮箱",
        "性别",
        "年龄",
        "部门",
        "单位",
        "公司",
        "职务",
        "签到",
        "签退",
        "签到时间",
        "签退时间",
    }
)
_CREDIT_CODE_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_CREDIT_CODE_WEIGHTS = (
    1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28
)


@dataclass(frozen=True)
class _Rule:
    id: str
    label: str
    pattern: re.Pattern[str]
    group: int = 0
    validator: Callable[[str], bool] | None = None


def _valid_cn_id(value: str) -> bool:
    value = value.upper()
    if not re.fullmatch(r"\d{17}[\dX]", value):
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = "10X98765432"
    total = sum(int(digit) * weight for digit, weight in zip(value[:17], weights))
    return checks[total % 11] == value[-1]


def _valid_luhn(value: str) -> bool:
    digits = re.sub(r"[\s-]", "", value)
    if not digits.isdigit() or not 16 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(map(int, digits)):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _valid_credit_code(value: str) -> bool:
    normalized = value.upper()
    if not re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", normalized):
        return False
    total = sum(
        _CREDIT_CODE_CHARS.index(character) * weight
        for character, weight in zip(normalized[:17], _CREDIT_CODE_WEIGHTS)
    )
    return _CREDIT_CODE_CHARS[(31 - total % 31) % 31] == normalized[-1]


def _valid_person(value: str) -> bool:
    if value in _PERSON_HEADER_WORDS:
        return False
    from ._candidate_classifier import classify_candidate

    return classify_candidate(
        "person",
        value,
        source="rule",
        structured=True,
    ).accepted


def _valid_private_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_private
    except ValueError:
        return False


_RULES = (
    _Rule(
        "email",
        "private_email",
        re.compile(
            r"(?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@"
            r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
            r"(?![A-Za-z0-9_.-])",
            re.I,
        ),
    ),
    _Rule(
        "cn_id",
        "account_number",
        re.compile(r"(?<!\d)\d{17}[\dXx](?![\dXx])"),
        validator=_valid_cn_id,
    ),
    _Rule(
        "bank_card",
        "account_number",
        re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)"),
        validator=_valid_luhn,
    ),
    _Rule(
        "mobile",
        "private_phone",
        re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    ),
    _Rule(
        "phone",
        "private_phone",
        re.compile(r"(?<!\d)0\d{2,3}[- ]\d{7,8}(?:-\d{1,6})?(?!\d)"),
    ),
    _Rule(
        "address",
        "private_address",
        re.compile(
            r"(?:家庭住址|住址|联系地址|地址)[：:\s]*"
            r"([^，。；;\n]{6,80})"
        ),
        group=1,
    ),
    _Rule(
        "person_name",
        "private_person",
        re.compile(
            rf"(?:员工姓名|客户姓名|联系人|法定代表人|负责人|收件人|姓名)"
            rf"[ \u3000]*[:：]?[ \u3000]*([{_CN}·]{{2,8}}|"
            rf"[A-Za-z][A-Za-z .'-]{{1,49}})"
        ),
        group=1,
        validator=_valid_person,
    ),
    _Rule(
        "organization_name",
        "private_organization",
        re.compile(
            r"(?:单位名称|企业名称|公司名称|供应商名称|客户单位)"
            r"[：: \t]*([^\t\r\n，。；;]{2,100})"
        ),
        group=1,
    ),
    _Rule(
        "organization_date",
        "organization_date",
        re.compile(
            r"(?:成立时间|成立日期|注册日期)[：: \t]*"
            r"(\d{4}[./-]\d{1,2}[./-]\d{1,2})"
        ),
        group=1,
    ),
    _Rule(
        "social_credit_code",
        "social_credit_code",
        re.compile(
            r"(?:统一社会信用代码|纳税人识别号)[：:\s]*"
            r"([0-9A-HJ-NPQRTUWXY]{18})",
            re.I,
        ),
        group=1,
        validator=_valid_credit_code,
    ),
    _Rule(
        "contract_code",
        "business_code",
        re.compile(
            r"(?:合同编号|合同号)[：:\s]*([A-Z0-9][A-Z0-9_./-]{2,49})",
            re.I,
        ),
        group=1,
    ),
    _Rule(
        "project_code",
        "business_code",
        re.compile(
            r"(?:项目编号|项目代号)[：:\s]*([A-Z0-9][A-Z0-9_./-]{2,49})",
            re.I,
        ),
        group=1,
    ),
    _Rule(
        "procurement_code",
        "business_code",
        re.compile(
            r"(?:采购单号|采购编号|招投标编号)[：:\s]*"
            r"([A-Z0-9][A-Z0-9_./-]{2,49})",
            re.I,
        ),
        group=1,
    ),
    _Rule(
        "employee_id",
        "employee_id",
        re.compile(
            r"(?:员工编号|员工号|工号)[：:\s]*([A-Z0-9][A-Z0-9_-]{2,29})",
            re.I,
        ),
        group=1,
    ),
    _Rule(
        "salary_amount",
        "salary_amount",
        re.compile(
            r"(?:月薪|工资|薪酬)(?:调整为|调整至|为|是)?[：:\s]*"
            r"((?:￥|¥)?\d[\d,]*(?:\.\d{1,2})?(?:万元|元|万)?)"
        ),
        group=1,
    ),
    _Rule(
        "bonus_amount",
        "bonus_amount",
        re.compile(
            r"奖金(?:调整为|调整至|为|是)?[：:\s]*"
            r"((?:￥|¥)?\d[\d,]*(?:\.\d{1,2})?(?:万元|元|万)?)"
        ),
        group=1,
    ),
    _Rule(
        "budget_amount",
        "budget_amount",
        re.compile(
            r"预算(?:调整为|调整至|为|是)?[：:\s]*"
            r"((?:￥|¥)?\d[\d,]*(?:\.\d{1,2})?(?:万元|元|万)?)"
        ),
        group=1,
    ),
    *(
        _Rule(
            rule_id,
            rule_id,
            re.compile(rf"{context}[：:\s]*([^，。；;\n]{{1,60}})"),
            group=1,
        )
        for rule_id, context in (
            ("performance_result", "绩效结果"),
            ("assessment_result", "考核结果"),
            ("leave_reason", "请假原因"),
            ("disciplinary_result", "奖惩结果"),
        )
    ),
    _Rule(
        "invoice_number",
        "invoice_number",
        re.compile(
            r"(?:发票号码|发票代码)[：:\s]*([A-Z0-9][A-Z0-9_-]{5,39})",
            re.I,
        ),
        group=1,
    ),
    _Rule(
        "payment_account",
        "payment_account",
        re.compile(
            r"(?:付款账号|收款账号|银行账号)[：:\s]*"
            r"([A-Z0-9][A-Z0-9_-]{5,39})",
            re.I,
        ),
        group=1,
    ),
    _Rule(
        "party_member_id",
        "party_member_id",
        re.compile(
            r"(?:党员编号|党员号)[：:\s]*([A-Z0-9][A-Z0-9_-]{2,29})",
            re.I,
        ),
        group=1,
    ),
    *(
        _Rule(
            rule_id,
            rule_id,
            re.compile(rf"{context}[：:\s]*([^，。；;\n]{{1,60}})"),
            group=1,
        )
        for rule_id, context in (
            ("party_fee", "党费金额"),
            ("democratic_review", "民主评议结果"),
            ("organization_relation", "组织关系状态"),
        )
    ),
    _Rule(
        "intranet",
        "intranet",
        re.compile(
            r"(?:服务器地址|内网IP|IP地址)[：:\s]*"
            r"((?:\d{1,3}\.){3}\d{1,3})",
            re.I,
        ),
        group=1,
        validator=_valid_private_ip,
    ),
)


def _iter_matches(
    text: str,
    enabled_rules: frozenset[str],
) -> Iterator[tuple[DetectedSpan, int]]:
    for priority, rule in enumerate(_RULES):
        if rule.id not in enabled_rules:
            continue
        for match in rule.pattern.finditer(text):
            value = match.group(rule.group)
            if rule.validator is not None and not rule.validator(value):
                continue
            start, end = match.span(rule.group)
            yield DetectedSpan(
                label=rule.label,
                start=start,
                end=end,
                text=value,
            ), priority


def detect(
    text: str,
    *,
    trim_whitespace: bool = True,
    enabled_rules: Iterable[str] | None = None,
) -> tuple[DetectedSpan, ...]:
    active = frozenset(ALL_RULE_IDS if enabled_rules is None else enabled_rules)
    candidates = sorted(
        _iter_matches(text, active),
        key=lambda item: (item[0].start, item[1], -item[0].end),
    )
    accepted: list[DetectedSpan] = []
    cursor = 0
    for span, _priority in candidates:
        start, end = span.start, span.end
        value = span.text
        if trim_whitespace:
            left = len(value) - len(value.lstrip())
            right = len(value) - len(value.rstrip())
            start += left
            end -= right
            value = text[start:end]
        if start < cursor or start >= end:
            continue
        accepted.append(
            DetectedSpan(
                label=span.label,
                start=start,
                end=end,
                text=value,
            )
        )
        cursor = end
    return tuple(accepted)
