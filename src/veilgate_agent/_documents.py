from __future__ import annotations

from dataclasses import dataclass
import csv
from html import unescape
import io
import os
from pathlib import Path
import re
from typing import Callable, Iterable
from xml.sax.saxutils import escape
import zipfile

from ._detector import detect as detect_rules
from ._hybrid import HybridFinding, HybridMapping, HybridMasker
from ._policy import blocker_message


TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".log",
        ".sql",
        ".xml",
        ".html",
        ".htm",
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".env",
        ".ini",
        ".conf",
        ".properties",
    }
)
CN_CHARACTER_RANGE = r"\u3400-\u4dbf\u4e00-\u9fff"
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".docx", ".xlsx", ".pdf"}
MAX_ARCHIVE_ENTRIES = 10_000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_ANALYZED_CHARACTERS = 200_000
MAX_COMPRESSION_RATIO = 200
REMOVABLE_METADATA_PREFIXES = (
    "_xmlsignatures/",
    "xl/persons/",
)
REMOVABLE_METADATA_PATHS = frozenset(
    {
        "word/people.xml",
        "word/commentsExtended.xml",
    }
)


class DocumentProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessedDocument:
    safe_path: Path
    mappings: tuple[HybridMapping, ...]
    engine_status: dict[str, object]
    output_format: str
    warnings: tuple[str, ...] = ()


TEXT_NODE_PATTERN = re.compile(
    r"<((?:[A-Za-z_][\w.-]*:)?(?:t|delText|instrText))"
    r"(\s[^>]*)?>([\s\S]*?)</\1>"
)
DIRECT_VALUE_PATTERN = re.compile(
    r"<((?:[A-Za-z_][\w.-]*:)?v)(\s[^>]*)?>([\s\S]*?)</\1>"
)
FORMULA_PATTERN = re.compile(
    r"<(?:[A-Za-z_][\w.-]*:)?f(?:\s[^>]*)?>([\s\S]*?)"
    r"</(?:[A-Za-z_][\w.-]*:)?f>"
)


def _xml_text(value: str) -> str:
    return unescape(value)


def _encoded_xml_text(value: str) -> str:
    return escape(value, {'"': "&quot;", "'": "&apos;"})


def _container_text(fragment: str, node_pattern: re.Pattern[str]) -> str:
    return "".join(_xml_text(match.group(3)) for match in node_pattern.finditer(fragment))


def _all_container_texts(
    xml: str,
    container_pattern: re.Pattern[str],
    node_pattern_for: Callable[[re.Match[str]], re.Pattern[str]],
) -> list[str]:
    return [
        _container_text(match.group(0), node_pattern_for(match))
        for match in container_pattern.finditer(xml)
    ]


def _mask_fragment(
    fragment: str,
    node_pattern: re.Pattern[str],
    masker: HybridMasker,
    semantic_terms: Iterable[HybridFinding],
) -> tuple[str, tuple[HybridMapping, ...]]:
    matches = list(node_pattern.finditer(fragment))
    if not matches:
        return fragment, ()
    originals = [_xml_text(match.group(3)) for match in matches]
    logical = "".join(originals)
    result = masker.mask(
        logical,
        semantic_terms=semantic_terms,
        use_nym=False,
    )
    if not result.findings:
        return fragment, ()
    values = list(originals)
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for value in originals:
        bounds.append((cursor, cursor + len(value)))
        cursor += len(value)
    for finding in reversed(result.findings):
        start_index = next(
            (
                index
                for index, (start, end) in enumerate(bounds)
                if start <= finding.start < end
            ),
            None,
        )
        end_index = next(
            (
                index
                for index, (start, end) in enumerate(bounds)
                if start < finding.end <= end
            ),
            None,
        )
        if start_index is None or end_index is None:
            continue
        start_offset = finding.start - bounds[start_index][0]
        end_offset = finding.end - bounds[end_index][0]
        if start_index == end_index:
            values[start_index] = (
                values[start_index][:start_offset]
                + finding.placeholder
                + values[start_index][end_offset:]
            )
        else:
            values[start_index] = (
                values[start_index][:start_offset] + finding.placeholder
            )
            for index in range(start_index + 1, end_index):
                values[index] = ""
            values[end_index] = values[end_index][end_offset:]
    output = fragment
    for match, value in reversed(list(zip(matches, values))):
        attributes = match.group(2) or ""
        replacement = (
            f"<{match.group(1)}{attributes}>"
            f"{_encoded_xml_text(value)}</{match.group(1)}>"
        )
        output = output[: match.start()] + replacement + output[match.end() :]
    return output, result.mappings


def _mask_containers(
    xml: str,
    container_pattern: re.Pattern[str],
    node_pattern_for: Callable[[re.Match[str]], re.Pattern[str]],
    masker: HybridMasker,
    semantic_terms: Iterable[HybridFinding],
) -> tuple[str, tuple[HybridMapping, ...]]:
    replacements: list[tuple[int, int, str]] = []
    mappings: list[HybridMapping] = []
    for match in container_pattern.finditer(xml):
        fragment, fragment_mappings = _mask_fragment(
            match.group(0),
            node_pattern_for(match),
            masker,
            semantic_terms,
        )
        if fragment != match.group(0):
            replacements.append((match.start(), match.end(), fragment))
        mappings.extend(fragment_mappings)
    output = xml
    for start, end, replacement in reversed(replacements):
        output = output[:start] + replacement + output[end:]
    return output, tuple(mappings)


DOCX_PART = re.compile(
    r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$"
)
DOCX_CONTAINER = re.compile(
    r"<((?:[A-Za-z_][\w.-]*:)?p)(?:\s[^>]*)?>[\s\S]*?</\1>"
)
DOCX_TABLE_CONTAINER = re.compile(
    r"<(?:(?:[A-Za-z_][\w.-]*):)?tbl(?:\s[^>]*)?>[\s\S]*?"
    r"</(?:(?:[A-Za-z_][\w.-]*):)?tbl>"
)
DOCX_ROW_CONTAINER = re.compile(
    r"<(?:(?:[A-Za-z_][\w.-]*):)?tr(?:\s[^>]*)?>[\s\S]*?"
    r"</(?:(?:[A-Za-z_][\w.-]*):)?tr>"
)
DOCX_CELL_CONTAINER = re.compile(
    r"<(?:(?:[A-Za-z_][\w.-]*):)?tc(?:\s[^>]*)?>[\s\S]*?"
    r"</(?:(?:[A-Za-z_][\w.-]*):)?tc>"
)
XLSX_SHEET = re.compile(r"^xl/worksheets/sheet\d+\.xml$")
XLSX_COMMENTS = re.compile(r"^xl/comments\d+\.xml$")
XLSX_SHARED_CONTAINER = re.compile(r"<si(?:\s[^>]*)?>[\s\S]*?</si>")
XLSX_CELL_CONTAINER = re.compile(
    r"<c(\s[^>]*)?(?<!/)>([\s\S]*?)</c>"
)
XLSX_COMMENT_CONTAINER = re.compile(
    r"<comment(?:\s[^>]*)?>[\s\S]*?</comment>"
)
XLSX_ROW_CONTAINER = re.compile(
    r"<(?:(?:[A-Za-z_][\w.-]*):)?row(?:\s[^>]*)?>[\s\S]*?"
    r"</(?:(?:[A-Za-z_][\w.-]*):)?row>"
)
CUSTOM_PROPERTIES_PATH = "docProps/custom.xml"
CUSTOM_PROPERTIES_RELS_PATH = "docProps/_rels/custom.xml.rels"
DOCX_SETTINGS_PATH = "word/settings.xml"

_XLSX_PERSON_HEADERS = frozenset(
    {
        "姓名",
        "员工姓名",
        "客户姓名",
        "联系人",
        "负责人",
        "法定代表人",
        "提名推荐人员",
        "推荐人员",
        "被推荐人",
        "推荐人选",
        "候选人",
        "获奖人员",
        "参演人员",
    }
)
_XLSX_ORGANIZATION_HEADERS = frozenset(
    {
        "公司",
        "单位",
        "机构",
        "组织",
        "单位名称",
        "企业名称",
        "公司名称",
        "供应商名称",
        "客户单位",
        "所属单位",
        "工作单位",
    }
)


def _normalize_xlsx_header(value: str) -> str:
    return re.sub(
        r"[\s：:]",
        "",
        re.sub(r"[（(](?:必填|选填)[）)]$", "", value.strip().lstrip("*＊")),
    )


def _xlsx_header_type(
    value: str,
    enabled_rules: Iterable[str],
) -> tuple[str, str] | None:
    enabled = frozenset(enabled_rules)
    normalized = _normalize_xlsx_header(value)
    if "person_name" in enabled and (
        normalized in _XLSX_PERSON_HEADERS
        or re.search(
            r"(?:姓名|联系人|负责人|经办人|申请人|收件人|审批人|开户人|"
            r"持卡人|法人|提名推荐人员|推荐人员|被推荐人|推荐人选|"
            r"候选人|获奖人员|参演人员)$",
            normalized,
        )
    ):
        return "private_person", "person"
    if "organization_name" in enabled and (
        normalized in _XLSX_ORGANIZATION_HEADERS
        or re.search(
            r"(?:单位|机构|组织|公司|企业|学校|医院|研究院|事务所|合作方)$",
            normalized,
        )
    ):
        return "private_organization", "company_name"
    return None


def _structured_terms_from_rows(
    rows: list[list[str]],
    enabled_rules: Iterable[str],
) -> tuple[HybridFinding, ...]:
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if any(_xlsx_header_type(value, enabled_rules) for value in row)
        ),
        None,
    )
    if header_index is None:
        return ()
    fields = {
        column: field
        for column, value in enumerate(rows[header_index])
        if (field := _xlsx_header_type(value, enabled_rules))
    }
    terms: dict[tuple[str, str], HybridFinding] = {}
    for row in rows[header_index + 1 :]:
        for column, value in enumerate(row):
            field = fields.get(column)
            normalized = value.strip()
            if field is None or not normalized:
                continue
            label, entity_type = field
            terms[(label, normalized)] = HybridFinding(
                label=label,
                start=0,
                end=len(normalized),
                text=normalized,
                source="structured",
                confidence=1.0,
                entity_type=entity_type,
            )
    return tuple(terms.values())


def _docx_structured_terms(
    source: Path,
    enabled_rules: Iterable[str],
) -> tuple[HybridFinding, ...]:
    terms: dict[tuple[str, str], HybridFinding] = {}
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if not DOCX_PART.match(info.filename):
                continue
            xml = archive.read(info.filename).decode("utf-8")
            for table in DOCX_TABLE_CONTAINER.finditer(xml):
                rows = [
                    [
                        _container_text(cell.group(0), TEXT_NODE_PATTERN)
                        for cell in DOCX_CELL_CONTAINER.finditer(row.group(0))
                    ]
                    for row in DOCX_ROW_CONTAINER.finditer(table.group(0))
                ]
                for finding in _structured_terms_from_rows(rows, enabled_rules):
                    terms[(finding.label, finding.text)] = finding
    return tuple(terms.values())


def _text_structured_terms(
    text: str,
    suffix: str,
    enabled_rules: Iterable[str],
) -> tuple[HybridFinding, ...]:
    rows: list[list[str]] = []
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        rows = [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    elif suffix in {".md", ".markdown"}:
        rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in text.splitlines()
            if line.count("|") >= 2
            and not re.fullmatch(r"[\s|:-]+", line)
        ]
    elif suffix == ".pdf":
        rows = [
            [cell.strip() for cell in re.split(r"\t+| {2,}", line.strip())]
            for line in text.splitlines()
            if re.search(r"\t+| {2,}", line)
        ]
    return _structured_terms_from_rows(rows, enabled_rules)


def _xlsx_column_index(reference: str, fallback: int) -> int:
    letters_match = re.match(r"[A-Z]+", reference, re.I)
    if letters_match is None:
        return fallback
    value = 0
    for character in letters_match.group(0).upper():
        value = value * 26 + ord(character) - 64
    return value - 1


def _xlsx_cell_text(
    match: re.Match[str],
    shared_strings: list[str],
) -> str:
    attributes = match.group(1) or ""
    body = match.group(2) or ""
    cell_type = re.search(r'\bt=(["\'])(.*?)\1', attributes)
    kind = cell_type.group(2) if cell_type else ""
    if kind == "inlineStr":
        return _container_text(body, TEXT_NODE_PATTERN)
    value_match = DIRECT_VALUE_PATTERN.search(body)
    if value_match is None:
        return ""
    value = _xml_text(value_match.group(3))
    if kind == "s" and value.isdigit():
        index = int(value)
        return shared_strings[index] if index < len(shared_strings) else ""
    return value if kind == "str" else ""


def _xlsx_structured_terms(
    source: Path,
    enabled_rules: Iterable[str],
) -> tuple[HybridFinding, ...]:
    terms: dict[tuple[str, str], HybridFinding] = {}
    with zipfile.ZipFile(source) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_xml = archive.read("xl/sharedStrings.xml").decode("utf-8")
            shared_strings = _all_container_texts(
                shared_xml,
                XLSX_SHARED_CONTAINER,
                lambda _match: TEXT_NODE_PATTERN,
            )
        for info in archive.infolist():
            if not XLSX_SHEET.match(info.filename):
                continue
            xml = archive.read(info.filename).decode("utf-8")
            rows: list[list[tuple[int, str]]] = []
            for row in XLSX_ROW_CONTAINER.finditer(xml):
                cells: list[tuple[int, str]] = []
                for fallback, cell in enumerate(
                    XLSX_CELL_CONTAINER.finditer(row.group(0))
                ):
                    attributes = cell.group(1) or ""
                    reference_match = re.search(r'\br=(["\'])(.*?)\1', attributes)
                    reference = reference_match.group(2) if reference_match else ""
                    cells.append(
                        (
                            _xlsx_column_index(reference, fallback),
                            _xlsx_cell_text(cell, shared_strings),
                        )
                    )
                rows.append(cells)
            dense_rows = [
                [
                    dict(row).get(column, "")
                    for column in range(max((item[0] for item in row), default=-1) + 1)
                ]
                for row in rows
            ]
            for finding in _structured_terms_from_rows(dense_rows, enabled_rules):
                terms[(finding.label, finding.text)] = finding
    return tuple(terms.values())


def _safe_zip_infos(source: Path) -> list[zipfile.ZipInfo]:
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise DocumentProcessingError(
            "Office 文件超过 64 MB；为避免本地模型长时间阻塞，"
            "请拆分文件后重试。"
        )
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise DocumentProcessingError("Office 文件损坏或不是有效的 OOXML 文件。") from error
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise DocumentProcessingError("Office 文件包含过多内部条目，已停止处理。")
    names = [item.filename for item in infos]
    if len(names) != len(set(names)):
        raise DocumentProcessingError("Office 文件包含重复内部路径，已停止处理。")
    if sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
        raise DocumentProcessingError("Office 文件解压后过大，已停止处理。")
    if any(
        item.file_size > 1024 * 1024
        and item.file_size / max(item.compress_size, 1) > MAX_COMPRESSION_RATIO
        for item in infos
    ):
        raise DocumentProcessingError("Office 文件压缩比异常，已停止处理。")
    unscanned = [
        item.filename
        for item in infos
        if re.match(
            r"^(?:word|xl)/(?:media|embeddings|activeX)/",
            item.filename,
        )
    ]
    if unscanned and os.environ.get("ANXINAI_ALLOW_UNSCANNED_MEDIA") != "1":
        raise DocumentProcessingError(
            "Office 文件包含图片、嵌入对象或 ActiveX 内容；"
            "当前没有 OCR/对象扫描能力，为避免原始敏感信息随文件发送，已停止处理。"
        )
    return infos


def _validate_ooxml(
    source: Path,
    *,
    required: set[str],
    blocked_patterns: tuple[re.Pattern[str], ...],
) -> None:
    infos = _safe_zip_infos(source)
    names = {item.filename for item in infos}
    missing = required - names
    if missing:
        raise DocumentProcessingError(
            "Office 文件缺少必要结构，已停止处理："
            + "、".join(sorted(missing))
        )
    blocked = sorted(
        name
        for name in names
        if any(pattern.match(name) for pattern in blocked_patterns)
    )
    if blocked:
        raise DocumentProcessingError(
            "Office 文件包含当前无法完整扫描的富文本或外部数据部件，"
            f"已停止处理：{blocked[0]}"
        )


def _scrub_package_metadata(path: str, data: bytes) -> bytes:
    if path == "docProps/core.xml":
        xml = data.decode("utf-8")
        tags = (
            "creator",
            "lastModifiedBy",
            "title",
            "subject",
            "description",
            "keywords",
            "category",
            "contentStatus",
        )
        for tag in tags:
            xml = re.sub(
                rf"<((?:[A-Za-z_][\w.-]*:)?{tag})(?:\s[^>]*)?>"
                rf"[\s\S]*?</\1>",
                rf"<\1></\1>",
                xml,
            )
        return xml.encode("utf-8")
    if path == "docProps/app.xml":
        xml = data.decode("utf-8")
        for tag in ("Company", "Manager", "HyperlinkBase"):
            xml = re.sub(
                rf"<((?:[A-Za-z_][\w.-]*:)?{tag})(?:\s[^>]*)?>"
                rf"[\s\S]*?</\1>",
                rf"<\1></\1>",
                xml,
            )
        return xml.encode("utf-8")
    return data


def _custom_property_text(data: bytes) -> str:
    xml = data.decode("utf-8")
    values = [
        _xml_text(match.group(2))
        for match in re.finditer(
            r'\bname=(["\'])(.*?)\1',
            xml,
        )
    ]
    values.extend(
        _xml_text(match.group(1)).strip()
        for match in re.finditer(r">([^<]+)<", xml)
        if _xml_text(match.group(1)).strip()
    )
    return "\n".join(values)


def _remove_custom_property_references(path: str, xml: str) -> str:
    if path == "[Content_Types].xml":
        return re.sub(
            r"<Override\b"
            r"(?=[^>]*\bPartName=(?:[\"'])/docProps/custom\.xml(?:[\"']))"
            r"[^>]*/>",
            "",
            xml,
            flags=re.I,
        )
    if path.endswith(".rels"):
        return re.sub(
            r"<Relationship\b"
            r"(?=[^>]*(?:"
            r"\bTarget=(?:[\"'])(?:/?docProps/)?custom\.xml(?:[\"'])|"
            r"\bType=(?:[\"'])[^\"']*/custom-properties(?:[\"'])"
            r"))[^>]*/>",
            "",
            xml,
            flags=re.I,
        )
    return xml


def _remove_custom_xml_references(path: str, xml: str) -> str:
    """Detach custom XML parts that are intentionally omitted from safe copies."""
    if path == "[Content_Types].xml":
        return re.sub(
            r"<Override\b"
            r"(?=[^>]*\bPartName=(?:[\"'])/customXml/[^\"']+(?:[\"']))"
            r"[^>]*/>",
            "",
            xml,
            flags=re.I,
        )
    if path.endswith(".rels"):
        return re.sub(
            r"<Relationship\b"
            r"(?=[^>]*(?:"
            r"\bTarget=(?:[\"'])[^\"']*customXml/[^\"']*(?:[\"'])|"
            r"\bType=(?:[\"'])[^\"']*/customXml(?:[\"'])"
            r"))[^>]*/>",
            "",
            xml,
            flags=re.I,
        )
    return xml


def _is_removable_office_metadata(path: str) -> bool:
    return path in REMOVABLE_METADATA_PATHS or path.startswith(
        REMOVABLE_METADATA_PREFIXES
    )


def _remove_office_metadata_references(path: str, xml: str) -> str:
    """Remove package references to metadata omitted from the safe copy."""
    if path == "[Content_Types].xml":
        xml = re.sub(
            r"<Override\b"
            r"(?=[^>]*\bPartName=(?:[\"'])/(?:"
            r"_xmlsignatures/[^\"']+|"
            r"word/(?:people|commentsExtended)\.xml|"
            r"xl/persons/[^\"']+"
            r")(?:[\"']))[^>]*/>",
            "",
            xml,
            flags=re.I,
        )
        return re.sub(
            r"<Default\b"
            r"(?=[^>]*\bExtension=(?:[\"'])sigs(?:[\"']))[^>]*/>",
            "",
            xml,
            flags=re.I,
        )
    if path.endswith(".rels"):
        return re.sub(
            r"<Relationship\b"
            r"(?=[^>]*(?:"
            r"\bTarget=(?:[\"'])[^\"']*(?:"
            r"_xmlsignatures/|people\.xml|commentsExtended\.xml|persons/"
            r")[^\"']*(?:[\"'])|"
            r"\bType=(?:[\"'])[^\"']*(?:"
            r"digital-signature/(?:origin|signature)|commentsExtended|person"
            r")(?:[\"'])"
            r"))[^>]*/>",
            "",
            xml,
            flags=re.I,
        )
    return xml


def _remove_docx_custom_xml_bindings(xml: str) -> str:
    return re.sub(
        r"<(?:[A-Za-z_][\w.-]*:)?dataBinding\b[^>]*/>",
        "",
        xml,
    )


def _document_variable_text(data: bytes) -> str:
    xml = data.decode("utf-8")
    values: list[str] = []
    for match in re.finditer(
        r"<(?:[A-Za-z_][\w.-]*:)?docVar\b[^>]*/?>",
        xml,
    ):
        fragment = match.group(0)
        for attribute in ("name", "val"):
            attribute_match = re.search(
                rf"\b(?:[A-Za-z_][\w.-]*:)?{attribute}=(['\"])(.*?)\1",
                fragment,
            )
            if attribute_match is not None:
                value = _xml_text(attribute_match.group(2)).strip()
                if value:
                    values.append(value)
    return "\n".join(values)


def _remove_document_variables(xml: str) -> str:
    return re.sub(
        r"<((?:[A-Za-z_][\w.-]*:)?docVars)\b[^>]*>"
        r"[\s\S]*?</\1>|"
        r"<(?:[A-Za-z_][\w.-]*:)?docVars\b[^>]*/>",
        "",
        xml,
    )


def _sanitize_external_relationships(
    xml: str,
    masker: HybridMasker,
) -> tuple[str, tuple[HybridMapping, ...]]:
    relationships = re.compile(r"<Relationship\b[^>]*/>")
    mappings: list[HybridMapping] = []
    replacements: list[tuple[int, int, str]] = []
    for match in relationships.finditer(xml):
        fragment = match.group(0)
        if not re.search(r'\bTargetMode=["\']External["\']', fragment):
            continue
        target_match = re.search(r'\bTarget=(["\'])(.*?)\1', fragment)
        if target_match is None:
            continue
        original = _xml_text(target_match.group(2))
        result = masker.mask(original, use_nym=False)
        mappings.extend(result.mappings)
        replacement_target = (
            result.text if result.findings else "https://example.invalid/redacted"
        )
        updated = (
            fragment[: target_match.start(2)]
            + _encoded_xml_text(replacement_target)
            + fragment[target_match.end(2) :]
        )
        replacements.append((match.start(), match.end(), updated))
    output = xml
    for start, end, replacement in reversed(replacements):
        output = output[:start] + replacement + output[end:]
    return output, tuple(mappings)


def _rewrite_zip(
    source: Path,
    target: Path,
    transform: Callable[
        [str, bytes],
        tuple[bytes | None, tuple[HybridMapping, ...]],
    ],
) -> tuple[HybridMapping, ...]:
    _safe_zip_infos(source)
    mappings: list[HybridMapping] = []
    try:
        with zipfile.ZipFile(source) as reader, zipfile.ZipFile(
            target, "w"
        ) as writer:
            for info in reader.infolist():
                data = reader.read(info)
                updated, entry_mappings = transform(info.filename, data)
                mappings.extend(entry_mappings)
                if updated is not None:
                    writer.writestr(info, updated)
    except DocumentProcessingError:
        if target.exists():
            target.unlink()
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        if target.exists():
            target.unlink()
        raise DocumentProcessingError("Office 文件本地处理失败。") from error
    os.chmod(target, 0o600)
    return tuple(mappings)


def _docx(
    source: Path,
    target: Path,
    masker: HybridMasker,
) -> ProcessedDocument:
    _validate_ooxml(
        source,
        required={"[Content_Types].xml", "_rels/.rels", "word/document.xml"},
        blocked_patterns=(
            re.compile(r"^word/(?:charts|diagrams|drawings|glossary|altChunk)/"),
            re.compile(r"^word/afchunk[^/]*\.(?:html?|mht|mhtml)$", re.I),
        ),
    )
    texts: list[str] = []
    has_custom_properties = False
    has_document_variables = False
    has_custom_xml = False
    has_digital_signatures = False
    has_collaboration_metadata = False
    with zipfile.ZipFile(source) as archive:
        _safe_zip_infos(source)
        for info in archive.infolist():
            if info.filename.startswith("customXml/"):
                has_custom_xml = True
                continue
            if info.filename.startswith("_xmlsignatures/"):
                has_digital_signatures = True
                continue
            if info.filename in REMOVABLE_METADATA_PATHS:
                has_collaboration_metadata = True
                continue
            if info.filename == CUSTOM_PROPERTIES_PATH:
                has_custom_properties = True
                texts.append(_custom_property_text(archive.read(info)))
                continue
            if info.filename == DOCX_SETTINGS_PATH:
                variable_text = _document_variable_text(archive.read(info))
                if variable_text:
                    has_document_variables = True
                    texts.append(variable_text)
                continue
            if not DOCX_PART.match(info.filename):
                continue
            xml = archive.read(info.filename).decode("utf-8")
            texts.extend(
                _all_container_texts(
                    xml,
                    DOCX_CONTAINER,
                    lambda _match: TEXT_NODE_PATTERN,
                )
            )
    semantic_text = "\n".join(texts)
    if len(semantic_text) > MAX_ANALYZED_CHARACTERS:
        raise DocumentProcessingError(
            "DOCX 可见文字超过 20 万字符；为保证 Nym 完整扫描，"
            "请拆分文档后重试。"
        )
    _enforce_fixed_blockers(semantic_text, masker)
    semantic_terms = (
        *masker.discover_semantic_terms(semantic_text),
        *_docx_structured_terms(source, masker.enabled_rules),
    )

    def transform(
        path: str,
        data: bytes,
    ) -> tuple[bytes | None, tuple[HybridMapping, ...]]:
        if path.startswith("customXml/"):
            return None, ()
        if _is_removable_office_metadata(path):
            return None, ()
        if path == CUSTOM_PROPERTIES_RELS_PATH:
            return None, ()
        if path == CUSTOM_PROPERTIES_PATH:
            custom_result = masker.mask(
                _custom_property_text(data),
                semantic_terms=semantic_terms,
            )
            return None, custom_result.mappings
        data = _scrub_package_metadata(path, data)
        if path == "[Content_Types].xml":
            xml = _remove_custom_property_references(
                path,
                data.decode("utf-8"),
            )
            xml = _remove_custom_xml_references(path, xml)
            xml = _remove_office_metadata_references(path, xml)
            return xml.encode("utf-8"), ()
        if path.endswith(".rels"):
            xml, mappings = _sanitize_external_relationships(
                data.decode("utf-8"), masker
            )
            xml = _remove_custom_property_references(path, xml)
            xml = _remove_custom_xml_references(path, xml)
            xml = _remove_office_metadata_references(path, xml)
            return xml.encode("utf-8"), mappings
        if path == DOCX_SETTINGS_PATH:
            variable_result = masker.mask(
                _document_variable_text(data),
                semantic_terms=semantic_terms,
            )
            xml = _remove_document_variables(data.decode("utf-8"))
            return xml.encode("utf-8"), variable_result.mappings
        if not DOCX_PART.match(path):
            return data, ()
        xml = data.decode("utf-8")
        xml = _remove_docx_custom_xml_bindings(xml)
        xml = re.sub(
            r'\b(?:[A-Za-z_][\w.-]*:)?author=(["\']).*?\1',
            'w:author="AnxinAI"',
            xml,
        )
        xml = re.sub(
            r'\b(?:[A-Za-z_][\w.-]*:)?initials=(["\']).*?\1',
            'w:initials="AI"',
            xml,
        )
        xml = re.sub(
            r"<((?:[A-Za-z_][\w.-]*:)?(?:tag|alias))\b[^>]*/>",
            r'<\1 w:val="AnxinAI"/>',
            xml,
        )
        updated, mappings = _mask_containers(
            xml,
            DOCX_CONTAINER,
            lambda _match: TEXT_NODE_PATTERN,
            masker,
            semantic_terms,
        )
        return updated.encode("utf-8"), mappings

    mappings = _rewrite_zip(source, target, transform)
    return ProcessedDocument(
        safe_path=target,
        mappings=mappings,
        engine_status=masker.engine_status,
        output_format="docx",
        warnings=(
            "图片、嵌入对象和扫描内容尚未执行 OCR。",
            *(
                ("已从安全版移除自定义文档属性。",)
                if has_custom_properties
                else ()
            ),
            *(
                ("已从安全版移除文档变量。",)
                if has_document_variables
                else ()
            ),
            *(
                ("已从安全版移除自定义 XML 部件及其关联。",)
                if has_custom_xml
                else ()
            ),
            *(
                ("已从安全版移除 Office 数字签名；安全副本改写后原签名本就会失效。",)
                if has_digital_signatures
                else ()
            ),
            *(
                ("已从安全版移除协作者身份和扩展评论元数据。",)
                if has_collaboration_metadata
                else ()
            ),
        ),
    )


def _xlsx_node_pattern(match: re.Match[str]) -> re.Pattern[str]:
    attributes = match.group(1) or ""
    return DIRECT_VALUE_PATTERN if re.search(r'\bt=["\']str["\']', attributes) else TEXT_NODE_PATTERN


def _xlsx_defined_name_detection_text(formula: str) -> str:
    """Remove Excel reference syntax while preserving real literals/constants."""
    sanitized = re.sub(
        r"'(?:[^']|'')+'!",
        "",
        formula,
    )
    sanitized = re.sub(
        rf"(?:[{CN_CHARACTER_RANGE}A-Za-z_]"
        rf"[{CN_CHARACTER_RANGE}A-Za-z0-9_. ]*)!",
        "",
        sanitized,
    )
    reference_patterns = (
        r"(?<![A-Za-z0-9_])\$?[A-Z]{1,3}\$?\d+"
        r"(?::\$?[A-Z]{1,3}\$?\d+)?(?![A-Za-z0-9_])",
        r"(?<![A-Za-z0-9_])\$?[A-Z]{1,3}:\$?[A-Z]{1,3}"
        r"(?![A-Za-z0-9_])",
        r"(?<![A-Za-z0-9_])\$?\d+:\$?\d+(?![A-Za-z0-9_])",
    )
    for pattern in reference_patterns:
        sanitized = re.sub(pattern, "", sanitized, flags=re.I)
    return sanitized


def _xlsx_structural_name_is_sensitive(
    value: str,
    enabled_rules: Iterable[str],
) -> bool:
    """Use deterministic high-signal rules for names that are Excel syntax."""
    return any(
        finding.label != "organization_date"
        for finding in detect_rules(value, enabled_rules=enabled_rules)
    )


def _enforce_fixed_blockers(text: str, masker: HybridMasker) -> None:
    message = blocker_message(
        text,
        credential_action=masker.credential_action,
    )
    if message is not None:
        raise DocumentProcessingError(message)


def _mask_numeric_xlsx_cells(
    xml: str,
    masker: HybridMasker,
) -> tuple[str, tuple[HybridMapping, ...]]:
    replacements: list[tuple[int, int, str]] = []
    mappings: list[HybridMapping] = []
    for match in XLSX_CELL_CONTAINER.finditer(xml):
        attributes = match.group(1) or ""
        body = match.group(2) or ""
        cell_type = re.search(r'\bt=["\']([^"\']+)["\']', attributes)
        if cell_type and cell_type.group(1) not in {"n"}:
            continue
        if FORMULA_PATTERN.search(body):
            continue
        value_match = DIRECT_VALUE_PATTERN.search(body)
        if value_match is None:
            continue
        value = _xml_text(value_match.group(3))
        result = masker.mask(value, use_nym=False)
        if not result.findings:
            continue
        updated_attributes = re.sub(
            r'\s+t=(["\']).*?\1',
            "",
            attributes,
        ) + ' t="inlineStr"'
        inline = f"<is><t>{_encoded_xml_text(result.text)}</t></is>"
        updated_body = (
            body[: value_match.start()] + inline + body[value_match.end() :]
        )
        updated = f"<c{updated_attributes}>{updated_body}</c>"
        replacements.append((match.start(), match.end(), updated))
        mappings.extend(result.mappings)
    output = xml
    for start, end, replacement in reversed(replacements):
        output = output[:start] + replacement + output[end:]
    return output, tuple(mappings)


def _xlsx(
    source: Path,
    target: Path,
    masker: HybridMasker,
) -> ProcessedDocument:
    _validate_ooxml(
        source,
        required={"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"},
        blocked_patterns=(
            re.compile(
                r"^xl/(?:charts|drawings|externalLinks|queryTables|pivotCache|"
                r"model|threadedComments|richData)/"
            ),
            re.compile(r"^xl/connections\.xml$"),
        ),
    )
    with zipfile.ZipFile(source) as archive:
        if not any(XLSX_SHEET.match(info.filename) for info in archive.infolist()):
            raise DocumentProcessingError("工作簿不包含可处理的工作表，已停止处理。")
    texts: list[str] = []
    has_custom_properties = False
    has_custom_xml = False
    has_digital_signatures = False
    has_collaboration_metadata = False
    with zipfile.ZipFile(source) as archive:
        _safe_zip_infos(source)
        for info in archive.infolist():
            data = archive.read(info.filename)
            if info.filename.startswith("customXml/"):
                has_custom_xml = True
            elif info.filename.startswith("_xmlsignatures/"):
                has_digital_signatures = True
            elif info.filename.startswith("xl/persons/"):
                has_collaboration_metadata = True
            elif info.filename == CUSTOM_PROPERTIES_PATH:
                has_custom_properties = True
                texts.append(_custom_property_text(data))
            elif info.filename == "xl/workbook.xml":
                xml = data.decode("utf-8")
                texts.extend(
                    _xml_text(match.group(2))
                    for match in re.finditer(
                        r'<sheet\b[^>]*\bname=(["\'])(.*?)\1',
                        xml,
                    )
                )
                texts.extend(
                    _xml_text(match.group(1))
                    for match in re.finditer(
                        r"<definedName(?:\s[^>]*)?>([\s\S]*?)</definedName>",
                        xml,
                    )
                )
            elif info.filename == "xl/sharedStrings.xml":
                xml = data.decode("utf-8")
                texts.extend(
                    _all_container_texts(
                        xml,
                        XLSX_SHARED_CONTAINER,
                        lambda _match: TEXT_NODE_PATTERN,
                    )
                )
            elif XLSX_SHEET.match(info.filename):
                xml = data.decode("utf-8")
                for formula in FORMULA_PATTERN.finditer(xml):
                    formula_text = _xml_text(formula.group(1))
                    texts.append(formula_text)
                    texts.append(
                        "".join(
                            _xml_text(quoted.group(2))
                            for quoted in re.finditer(
                                r'(["\'])(.*?)\1',
                                formula_text,
                            )
                        )
                    )
                texts.extend(
                    _all_container_texts(
                        xml,
                        XLSX_CELL_CONTAINER,
                        _xlsx_node_pattern,
                    )
                )
            elif XLSX_COMMENTS.match(info.filename):
                xml = data.decode("utf-8")
                texts.extend(
                    _all_container_texts(
                        xml,
                        XLSX_COMMENT_CONTAINER,
                        lambda _match: TEXT_NODE_PATTERN,
                    )
                )
    semantic_text = "\n".join(texts)
    if len(semantic_text) > MAX_ANALYZED_CHARACTERS:
        raise DocumentProcessingError(
            "XLSX 可见文字超过 20 万字符；为保证 Nym 完整扫描，"
            "请拆分工作簿后重试。"
        )
    _enforce_fixed_blockers(semantic_text, masker)
    semantic_terms = (
        *masker.discover_semantic_terms(semantic_text),
        *_xlsx_structured_terms(source, masker.enabled_rules),
    )

    def transform(
        path: str,
        data: bytes,
    ) -> tuple[bytes | None, tuple[HybridMapping, ...]]:
        if path.startswith("customXml/"):
            return None, ()
        if _is_removable_office_metadata(path):
            return None, ()
        if path == CUSTOM_PROPERTIES_RELS_PATH:
            return None, ()
        if path == CUSTOM_PROPERTIES_PATH:
            custom_result = masker.mask(
                _custom_property_text(data),
                semantic_terms=semantic_terms,
            )
            return None, custom_result.mappings
        data = _scrub_package_metadata(path, data)
        if path == "[Content_Types].xml":
            xml = _remove_custom_property_references(
                path,
                data.decode("utf-8"),
            )
            xml = _remove_custom_xml_references(path, xml)
            xml = _remove_office_metadata_references(path, xml)
            return xml.encode("utf-8"), ()
        if path.endswith(".rels"):
            xml, mappings = _sanitize_external_relationships(
                data.decode("utf-8"), masker
            )
            xml = _remove_custom_property_references(path, xml)
            xml = _remove_custom_xml_references(path, xml)
            xml = _remove_office_metadata_references(path, xml)
            return xml.encode("utf-8"), mappings
        if path == "xl/workbook.xml":
            xml = data.decode("utf-8")
            sheet_names = [
                _xml_text(match.group(2))
                for match in re.finditer(
                    r'<sheet\b[^>]*\bname=(["\'])(.*?)\1',
                    xml,
                )
            ]
            if any(
                _xlsx_structural_name_is_sensitive(
                    sheet_name,
                    masker.enabled_rules,
                )
                for sheet_name in sheet_names
            ):
                raise DocumentProcessingError(
                    "工作表 Tab 名中包含敏感信息；为避免公式引用损坏，"
                    "已停止处理，请先在 Excel 中重命名对应工作表。"
                )
            defined_names = list(
                re.finditer(
                    r"<definedName(\s[^>]*)?>([\s\S]*?)</definedName>",
                    xml,
                )
            )
            for index, defined_name in enumerate(defined_names, start=1):
                attributes = defined_name.group(1) or ""
                name_match = re.search(r'\bname=(["\'])(.*?)\1', attributes)
                identifier = (
                    _xml_text(name_match.group(2)) if name_match else ""
                )
                formula = _xml_text(defined_name.group(2))
                detection_text = _xlsx_defined_name_detection_text(formula)
                if identifier and _xlsx_structural_name_is_sensitive(
                    identifier,
                    masker.enabled_rules,
                ):
                    raise DocumentProcessingError(
                        f"第 {index} 个定义名称本身包含敏感信息；"
                        "为避免公式引用损坏，已停止处理，请在名称管理器中重命名。"
                    )
                if detection_text and masker.mask(
                    detection_text,
                    semantic_terms=semantic_terms,
                    use_nym=False,
                ).findings:
                    raise DocumentProcessingError(
                        f"第 {index} 个定义名称的公式内容包含敏感信息；"
                        "已停止处理，请在名称管理器中检查。"
                    )
            return data, ()
        if path == "xl/sharedStrings.xml":
            xml = data.decode("utf-8")
            updated, mappings = _mask_containers(
                xml,
                XLSX_SHARED_CONTAINER,
                lambda _match: TEXT_NODE_PATTERN,
                masker,
                semantic_terms,
            )
            return updated.encode("utf-8"), mappings
        if XLSX_SHEET.match(path):
            xml = data.decode("utf-8")
            for cell in XLSX_CELL_CONTAINER.finditer(xml):
                formula = FORMULA_PATTERN.search(cell.group(0))
                if formula is None:
                    continue
                formula_text = _xml_text(formula.group(1))
                cached = "".join(
                    _xml_text(value.group(3))
                    for value in DIRECT_VALUE_PATTERN.finditer(cell.group(0))
                )
                literals = "".join(
                    _xml_text(quoted.group(2))
                    for quoted in re.finditer(
                        r'(["\'])(.*?)\1',
                        formula_text,
                    )
                )
                candidates = (formula_text, cached, literals)
                if re.search(
                    r"\b(?:CHAR|UNICHAR|CODE|UNICODE)\s*\(",
                    formula_text,
                    re.I,
                ):
                    raise DocumentProcessingError(
                        "工作簿公式使用字符编码函数，无法可靠判断重算结果；"
                        "已停止处理，请先把相关公式转换为值。"
                    )
                if any(
                    masker.mask(
                        candidate,
                        semantic_terms=semantic_terms,
                        use_nym=True,
                    ).findings
                    for candidate in candidates
                    if candidate
                ):
                    raise DocumentProcessingError(
                        "工作簿公式中包含敏感信息，或公式缓存值包含敏感信息；"
                        "为避免重新计算后恢复原值，已停止处理，"
                        "请先把相关公式转换为值。"
                    )
                formula_result = masker.mask(
                    formula_text,
                    semantic_terms=semantic_terms,
                    use_nym=False,
                )
                if formula_result.findings:
                    raise DocumentProcessingError(
                        "工作簿公式中包含敏感信息；为避免重新计算后恢复原值，"
                        "已停止处理，请先把相关公式转换为值。"
                    )
            xml, numeric_mappings = _mask_numeric_xlsx_cells(xml, masker)
            updated, mappings = _mask_containers(
                xml,
                XLSX_CELL_CONTAINER,
                _xlsx_node_pattern,
                masker,
                semantic_terms,
            )
            return updated.encode("utf-8"), (*numeric_mappings, *mappings)
        if XLSX_COMMENTS.match(path):
            xml = data.decode("utf-8")
            xml = re.sub(
                r"<author(?:\s[^>]*)?>[\s\S]*?</author>",
                "<author>AnxinAI</author>",
                xml,
            )
            updated, mappings = _mask_containers(
                xml,
                XLSX_COMMENT_CONTAINER,
                lambda _match: TEXT_NODE_PATTERN,
                masker,
                semantic_terms,
            )
            return updated.encode("utf-8"), mappings
        return data, ()

    mappings = _rewrite_zip(source, target, transform)
    return ProcessedDocument(
        safe_path=target,
        mappings=mappings,
        engine_status=masker.engine_status,
        output_format="xlsx",
        warnings=(
            "图片、图表文本和嵌入对象尚未执行 OCR。",
            *(
                ("已从安全版移除自定义文档属性。",)
                if has_custom_properties
                else ()
            ),
            *(
                ("已从安全版移除自定义 XML 部件及其关联。",)
                if has_custom_xml
                else ()
            ),
            *(
                ("已从安全版移除 Office 数字签名；安全副本改写后原签名本就会失效。",)
                if has_digital_signatures
                else ()
            ),
            *(
                ("已从安全版移除协作者身份元数据。",)
                if has_collaboration_metadata
                else ()
            ),
        ),
    )


def _pdf(
    source: Path,
    target: Path,
    masker: HybridMasker,
) -> ProcessedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise DocumentProcessingError(
            "PDF 解析依赖缺失，请安装 pypdf。"
        ) from error
    try:
        reader = PdfReader(str(source))
        if reader.is_encrypted:
            raise DocumentProcessingError("加密 PDF 暂不支持，已停止处理。")
        pages = []
        for page in reader.pages:
            if (
                os.environ.get("ANXINAI_ALLOW_UNSCANNED_MEDIA") != "1"
                and len(page.images) > 0
            ):
                raise DocumentProcessingError(
                    "PDF 包含图片；当前没有 OCR 能力，为避免图片中的敏感信息"
                    "随文件发送，已停止处理。"
                )
            pages.append((page.extract_text() or "").strip())
    except DocumentProcessingError:
        raise
    except Exception as error:
        raise DocumentProcessingError("PDF 本地解析失败。") from error
    if not any(pages):
        raise DocumentProcessingError(
            "PDF 未提取到文字，可能是扫描版或图片型 PDF；未启用 OCR，已停止处理。"
        )
    extracted_text = "\n".join(pages)
    if len(extracted_text) > MAX_ANALYZED_CHARACTERS:
        raise DocumentProcessingError(
            "PDF 可提取文字超过 20 万字符；为保证 Nym 完整扫描，"
            "请拆分 PDF 后重试。"
        )
    _enforce_fixed_blockers(extracted_text, masker)
    markdown = "\n\n".join(
        ["# 安全版文档", *[
            f"## 第 {index} 页\n\n{page}"
            for index, page in enumerate(pages, start=1)
        ]]
    )
    structured_terms = _text_structured_terms(
        extracted_text,
        ".pdf",
        masker.enabled_rules,
    )
    result = masker.mask(markdown, semantic_terms=structured_terms)
    target.write_text(result.text, encoding="utf-8")
    os.chmod(target, 0o600)
    return ProcessedDocument(
        safe_path=target,
        mappings=result.mappings,
        engine_status=masker.engine_status,
        output_format="markdown",
        warnings=(
            "PDF 安全版转换为 Markdown，不保留原始排版。",
            "矢量轮廓文字和未被 PDF 文本层暴露的内容无法检测。",
        ),
    )


def process_document(
    source: Path,
    target: Path,
    *,
    enable_nym: bool = True,
) -> ProcessedDocument:
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentProcessingError(f"暂不支持 {suffix or '无扩展名'} 文件。")
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise DocumentProcessingError(
            "文件超过 64 MB；为避免本地模型长时间阻塞，请拆分后重试。"
        )
    masker = HybridMasker(enable_nym=enable_nym)
    if (
        enable_nym
        and os.environ.get("ANXINAI_DISABLE_NYM") != "1"
        and masker.nym is None
        and os.environ.get("ANXINAI_ALLOW_RULES_ONLY") != "1"
    ):
        raise DocumentProcessingError(
            f"Nym Edge INT8 未就绪：{masker.nym_error or '未知错误'}"
        )
    if suffix in TEXT_SUFFIXES:
        try:
            original = source.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise DocumentProcessingError("文件不是 UTF-8 文本，已停止处理。") from error
        if len(original) > MAX_ANALYZED_CHARACTERS:
            raise DocumentProcessingError(
                "文本超过 20 万字符；为保证 Nym 完整扫描，请拆分文件后重试。"
            )
        _enforce_fixed_blockers(original, masker)
        semantic_terms = _text_structured_terms(
            original,
            suffix,
            masker.enabled_rules,
        )
        result = masker.mask(original, semantic_terms=semantic_terms)
        target.write_text(result.text, encoding="utf-8")
        os.chmod(target, 0o600)
        return ProcessedDocument(
            safe_path=target,
            mappings=result.mappings,
            engine_status=masker.engine_status,
            output_format=suffix.lstrip("."),
        )
    if suffix == ".docx":
        return _docx(source, target, masker)
    if suffix == ".xlsx":
        return _xlsx(source, target, masker)
    if suffix == ".pdf":
        markdown_target = target.with_suffix(".md")
        if target != markdown_target and target.exists():
            target.unlink()
        return _pdf(source, markdown_target, masker)
    raise AssertionError("unsupported suffix reached")
