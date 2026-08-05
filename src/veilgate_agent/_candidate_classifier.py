from __future__ import annotations

from dataclasses import dataclass
import re


MODEL_VERSION = "precision-v1"

_COMMON_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏窦章云苏潘葛奚范彭郎鲁韦昌马苗方俞任袁柳鲍史唐费廉岑"
    "薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄穆萧"
    "尹姚邵汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵"
    "席季麻强贾路娄江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万柯管卢"
    "莫经房解应宗丁宣邓郁单杭洪包左石崔吉龚程邢裴陆荣翁荀羊甄曲家封"
    "储靳段富巫乌焦巴弓牧山谷车侯全班仰秋仲伊宫宁仇栾甘祖武符刘景詹"
    "龙叶黎乔闻党翟谭贡劳姬申扶冉雍桑桂牛寿边燕浦尚农温庄晏柴瞿阎连"
    "茹习艾鱼向古易慎戈廖都耿满弘匡国文寇广东欧利蔚越隆师巩聂晁敖融"
    "冷辛那简饶曾沙鞠关查后荆红游竺权盖益桓公覃蒙"
)
_COMPOUND_SURNAMES = ("欧阳", "司马", "上官", "诸葛")
_PERSON_NEGATIVES = frozenset(
    {
        "参加",
        "重申",
        "重申演练流程",
        "召开",
        "组织",
        "负责",
        "出席",
        "执行",
        "通知",
        "确认",
        "审核",
        "申请",
        "宣布",
        "签署",
        "提交",
    }
)
_ORG_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "有限公司",
    "集团公司",
    "委员会",
    "基金会",
    "研究院",
    "事务所",
    "合作社",
    "联合会",
    "协会",
    "学会",
    "集团",
    "公司",
    "企业",
    "机构",
    "中心",
    "银行",
    "医院",
    "学校",
    "大学",
    "学院",
    "研究所",
    "管理局",
    "财政局",
    "公安局",
    "办公室",
)
_ADDRESS_MARKERS = (
    "省",
    "市",
    "区",
    "县",
    "镇",
    "乡",
    "街道",
    "街",
    "路",
    "巷",
    "村",
    "号",
    "楼",
    "室",
)
_EXPLICIT_FIELD = re.compile(
    r"(?:姓名|员工姓名|客户姓名|联系人|法定代表人|负责人|收件人|"
    r"单位名称|企业名称|公司名称|供应商名称|客户单位|所属单位|工作单位|"
    r"家庭住址|住址|联系地址|地址)[ \u3000]*[:：][ \u3000]*$"
)


@dataclass(frozen=True)
class CandidateDecision:
    accepted: bool
    score: float
    model_version: str = MODEL_VERSION


def _explicit_field_before(text: str, start: int) -> bool:
    return bool(_EXPLICIT_FIELD.search(text[max(0, start - 24) : start]))


def _is_chinese_person(value: str) -> bool:
    if value in _PERSON_NEGATIVES or not re.fullmatch(r"[\u3400-\u9fff·]{2,4}", value):
        return False
    if value.startswith(_COMPOUND_SURNAMES):
        return 3 <= len(value) <= 4
    return value[0] in _COMMON_SURNAMES and 2 <= len(value) <= 3


def _is_latin_person(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Za-z][A-Za-z'-]{1,24}(?:[ .][A-Za-z][A-Za-z'-]{1,24})+",
            value,
        )
    )


def classify_candidate(
    entity_type: str,
    value: str,
    *,
    text: str = "",
    start: int = 0,
    confidence: float | None = None,
    source: str = "nym",
    structured: bool = False,
) -> CandidateDecision:
    """High-precision candidate verifier; uncertain semantic spans are retained."""
    normalized = value.strip()
    probability = float(confidence if confidence is not None else 1.0)
    explicit = _explicit_field_before(text, start)
    structured = structured or source == "structured"

    if entity_type == "person":
        shape_ok = _is_chinese_person(normalized) or _is_latin_person(normalized)
        if not shape_ok:
            return CandidateDecision(False, 0.0)
        if source == "nym":
            accepted = probability >= 0.80
        elif source == "rule":
            accepted = True
        else:
            accepted = structured or explicit
        return CandidateDecision(accepted, probability if accepted else 0.0)

    if entity_type == "company_name":
        if structured:
            accepted = bool(
                2 <= len(normalized) <= 40
                and re.fullmatch(
                    r"[\u3400-\u9fffA-Za-z0-9&·（）()\-— ]+",
                    normalized,
                )
            )
            return CandidateDecision(accepted, 1.0 if accepted else 0.0)
        has_suffix = any(normalized.endswith(suffix) for suffix in _ORG_SUFFIXES)
        if not has_suffix:
            return CandidateDecision(False, 0.0)
        threshold = 0.78 if source == "nym" else 0.0
        accepted = probability >= threshold and len(normalized) >= 3
        return CandidateDecision(accepted, probability if accepted else 0.0)

    if entity_type == "address":
        has_structure = any(marker in normalized for marker in _ADDRESS_MARKERS)
        if not has_structure or len(normalized) < 5:
            return CandidateDecision(False, 0.0)
        threshold = 0.78 if source == "nym" else 0.0
        accepted = probability >= threshold and (source == "nym" or structured or explicit)
        return CandidateDecision(accepted, probability if accepted else 0.0)

    return CandidateDecision(True, probability)
