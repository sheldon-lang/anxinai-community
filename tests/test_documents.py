import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from veilgate_agent._documents import DocumentProcessingError, process_document


CONTENT_TYPES_DOCX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
ROOT_RELS_DOCX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
CONTENT_TYPES_XLSX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""


class DocumentPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_disable_nym = os.environ.get("ANXINAI_DISABLE_NYM")
        os.environ["ANXINAI_DISABLE_NYM"] = "1"

    def tearDown(self):
        if self.previous_disable_nym is None:
            os.environ.pop("ANXINAI_DISABLE_NYM", None)
        else:
            os.environ["ANXINAI_DISABLE_NYM"] = self.previous_disable_nym
        self.temporary.cleanup()

    @property
    def root(self) -> Path:
        return Path(self.temporary.name)

    def make_docx(
        self,
        *,
        collaboration_metadata: bool = False,
        custom_properties: bool = False,
        custom_xml: bool = False,
        digital_signature: bool = False,
        document_variables: bool = False,
        recommendation_table: bool = False,
    ) -> Path:
        source = self.root / "客户资料.docx"
        table_xml = """
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>公司</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>提名推荐人员</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>推荐理由</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>天府通</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>王力</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>负责演练组织工作并参加复盘。</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>""" if recommendation_table else ""
        document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>联系人张</w:t></w:r><w:r><w:t>伟，手机号13800138000</w:t></w:r></w:p>
    <w:p><w:del><w:r><w:delText>旧号码13912345678</w:delText></w:r></w:del></w:p>
    {table_xml}
    <w:sectPr/>
  </w:body>
</w:document>"""
        content_types = CONTENT_TYPES_DOCX
        root_rels = ROOT_RELS_DOCX
        if custom_properties:
            content_types = content_types.replace(
                "</Types>",
                '<Override PartName="/docProps/custom.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'custom-properties+xml"/></Types>',
            )
            root_rels = root_rels.replace(
                "</Relationships>",
                '<Relationship Id="rIdCustom" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/custom-properties" '
                'Target="docProps/custom.xml"/></Relationships>',
            )
        if custom_xml:
            content_types = content_types.replace(
                "</Types>",
                '<Override PartName="/customXml/item1.xml" '
                'ContentType="application/xml"/></Types>',
            )
            root_rels = root_rels.replace(
                "</Relationships>",
                '<Relationship Id="rIdCustomXml" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/customXml" '
                'Target="customXml/item1.xml"/></Relationships>',
            )
        if digital_signature:
            content_types = content_types.replace(
                "</Types>",
                '<Default Extension="sigs" '
                'ContentType="application/vnd.openxmlformats-package.'
                'digital-signature-xmlsignature+xml"/>'
                '<Override PartName="/_xmlsignatures/origin.sigs" '
                'ContentType="application/vnd.openxmlformats-package.'
                'digital-signature-origin"/></Types>',
            )
            root_rels = root_rels.replace(
                "</Relationships>",
                '<Relationship Id="rIdSignature" '
                'Type="http://schemas.openxmlformats.org/package/2006/'
                'relationships/digital-signature/origin" '
                'Target="_xmlsignatures/origin.sigs"/></Relationships>',
            )
        if collaboration_metadata:
            content_types = content_types.replace(
                "</Types>",
                '<Override PartName="/word/people.xml" '
                'ContentType="application/vnd.ms-word.person+xml"/>'
                '<Override PartName="/word/commentsExtended.xml" '
                'ContentType="application/vnd.ms-word.commentsExt+xml"/>'
                '</Types>',
            )
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("word/document.xml", document)
            if collaboration_metadata:
                archive.writestr(
                    "word/_rels/document.xml.rels",
                    """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdPeople" Type="http://schemas.microsoft.com/office/2011/relationships/person" Target="people.xml"/>
  <Relationship Id="rIdCommentsExt" Type="http://schemas.microsoft.com/office/2011/relationships/commentsExtended" Target="commentsExtended.xml"/>
</Relationships>""",
                )
                archive.writestr("word/people.xml", "<people><person/></people>")
                archive.writestr(
                    "word/commentsExtended.xml",
                    "<commentsEx><commentEx/></commentsEx>",
                )
            if document_variables:
                archive.writestr(
                    "word/settings.xml",
                    """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:zoom w:percent="100"/>
  <w:docVars>
    <w:docVar w:name="备用联系人" w:val="李娜 13600002222"/>
  </w:docVars>
</w:settings>""",
                )
            if custom_properties:
                archive.writestr(
                    "docProps/custom.xml",
                    """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="备用手机号"><vt:lpwstr>13700001111</vt:lpwstr></property>
</Properties>""",
                )
                archive.writestr(
                    "docProps/_rels/custom.xml.rels",
                    """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""",
                )
            if custom_xml:
                archive.writestr(
                    "customXml/item1.xml",
                    '<root><hidden>13800138000</hidden></root>',
                )
            if digital_signature:
                archive.writestr("_xmlsignatures/origin.sigs", "<SignatureOrigin/>")
                archive.writestr(
                    "_xmlsignatures/_rels/origin.sigs.rels",
                    """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/signature" Target="sig1.xml"/>
</Relationships>""",
                )
                archive.writestr("_xmlsignatures/sig1.xml", "<Signature/>")
                archive.writestr(
                    "customXml/_rels/item1.xml.rels",
                    """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="urn:example" Target="https://example.com/schema" TargetMode="External"/>
</Relationships>""",
                )
        return source

    def make_xlsx(
        self,
        formula: str | None = None,
        *,
        numeric_phone: bool = False,
        sheet_name: str = "客户",
        defined_name: tuple[str, str] | None = None,
    ) -> Path:
        source = self.root / "客户资料.xlsx"
        root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
        sheet_name_xml = (
            sheet_name.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("\t", "&#9;")
        )
        defined_names_xml = (
            (
                "<definedNames><definedName "
                f'name="{defined_name[0]}">{defined_name[1]}</definedName>'
                "</definedNames>"
            )
            if defined_name
            else ""
        )
        workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="{sheet_name_xml}" sheetId="1" r:id="rId1"/></sheets>
  {defined_names_xml}
</workbook>"""
        workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
        formula_cell = (
            f'<c r="C2" t="str"><f>{formula}</f><v>结果</v></c>'
            if formula is not None
            else ""
        )
        phone_cell = (
            '<c r="B2"><v>13800138000</v></c>'
            if numeric_phone
            else '<c r="B2" t="inlineStr"><is><t>13800138000</t></is></c>'
        )
        sheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>姓名</t></is></c><c r="B1" t="inlineStr"><is><t>手机号</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>联系人张伟</t></is></c>{phone_cell}{formula_cell}</row>
  </sheetData>
</worksheet>"""
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES_XLSX)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        return source

    def make_recommendation_xlsx(self) -> Path:
        source = self.root / "推荐名单.xlsx"
        root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
        workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="推荐名单" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
        workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

        def cell(reference: str, value: str) -> str:
            return (
                f'<c r="{reference}" t="inlineStr"><is><t>'
                f'{value}</t></is></c>'
            )

        rows = [
            ("天府市民云", "李成", "积极参与演练并完成关键环节。"),
            ("天府通", "王力", "组织召开复盘会议，表现突出。"),
            ("交子金科", "覃雄", "负责环境搭建、测试和联调工作。"),
        ]
        row_xml = [
            "<row r=\"1\">"
            + cell("A1", "公司")
            + cell("B1", "提名推荐人员")
            + cell("C1", "推荐理由")
            + "</row>"
        ]
        for index, (company, person, reason) in enumerate(rows, start=2):
            row_xml.append(
                f'<row r="{index}">'
                + cell(f"A{index}", company)
                + cell(f"B{index}", person)
                + cell(f"C{index}", reason)
                + "</row>"
            )
        sheet = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(row_xml)}</sheetData></worksheet>"
        )
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES_XLSX)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        return source

    def test_docx_preserves_package_and_masks_across_runs(self):
        source = self.make_docx()
        target = self.root / "客户资料-安全版.docx"
        result = process_document(source, target)
        self.assertEqual(result.output_format, "docx")
        self.assertTrue(target.is_file())
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("[VG_PRIVATE_PERSON_1]", xml)
            self.assertIn("[VG_PRIVATE_PHONE_1]", xml)
            self.assertNotIn("13800138000", xml)
            self.assertNotIn("13912345678", xml)
            self.assertEqual(archive.testzip(), None)
        with zipfile.ZipFile(source) as archive:
            self.assertIn("13800138000", archive.read("word/document.xml").decode())

    def test_xlsx_uses_headers_as_strong_context_for_short_names_and_brands(self):
        source = self.make_recommendation_xlsx()
        target = self.root / "推荐名单-安全版.xlsx"
        result = process_document(source, target)
        originals = {mapping.original for mapping in result.mappings}
        self.assertTrue(
            {"天府市民云", "天府通", "交子金科", "李成", "王力", "覃雄"}
            <= originals
        )
        self.assertNotIn("参加", originals)
        self.assertNotIn("组织", originals)
        self.assertNotIn("负责", originals)
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            for original in originals:
                self.assertNotIn(original, xml)
            self.assertIn("积极参与演练并完成关键环节", xml)
            self.assertIn("组织召开复盘会议", xml)

    def test_docx_table_uses_headers_as_strong_context(self):
        source = self.make_docx(recommendation_table=True)
        target = self.root / "推荐材料-安全版.docx"
        result = process_document(source, target)
        originals = {mapping.original for mapping in result.mappings}
        self.assertIn("天府通", originals)
        self.assertIn("王力", originals)
        self.assertNotIn("负责", originals)
        self.assertNotIn("参加", originals)
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertNotIn("天府通", xml)
        self.assertNotIn("王力", xml)
        self.assertIn("负责演练组织工作并参加复盘", xml)

    def test_csv_uses_headers_as_strong_context(self):
        source = self.root / "推荐名单.csv"
        source.write_text(
            "公司,提名推荐人员,推荐理由\n"
            "天府通,王力,负责演练组织工作并参加复盘\n",
            encoding="utf-8",
        )
        target = self.root / "推荐名单-安全版.csv"
        result = process_document(source, target)
        originals = {mapping.original for mapping in result.mappings}
        self.assertIn("天府通", originals)
        self.assertIn("王力", originals)
        self.assertNotIn("负责", originals)
        self.assertNotIn("参加", originals)
        safe = target.read_text(encoding="utf-8")
        self.assertNotIn("天府通", safe)
        self.assertNotIn("王力", safe)
        self.assertIn("负责演练组织工作并参加复盘", safe)

    def test_docx_custom_properties_are_detected_and_removed(self):
        source = self.make_docx(custom_properties=True)
        target = self.root / "客户资料-安全版.docx"
        result = process_document(source, target)
        self.assertIn("已从安全版移除自定义文档属性。", result.warnings)
        self.assertIn(
            "13700001111",
            {mapping.original for mapping in result.mappings},
        )
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            self.assertNotIn("docProps/custom.xml", names)
            self.assertNotIn("docProps/_rels/custom.xml.rels", names)
            self.assertNotIn(
                "custom.xml",
                archive.read("[Content_Types].xml").decode("utf-8"),
            )
            self.assertNotIn(
                "custom-properties",
                archive.read("_rels/.rels").decode("utf-8"),
            )
            self.assertEqual(archive.testzip(), None)

    def test_docx_custom_xml_is_removed_without_blocking_visible_content(self):
        source = self.make_docx(custom_xml=True)
        target = self.root / "客户资料-安全版.docx"
        result = process_document(source, target)
        self.assertIn("已从安全版移除自定义 XML 部件及其关联。", result.warnings)
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            self.assertFalse(any(name.startswith("customXml/") for name in names))
            self.assertNotIn(
                "customXml",
                archive.read("[Content_Types].xml").decode("utf-8"),
            )
            self.assertNotIn(
                "customXml",
                archive.read("_rels/.rels").decode("utf-8"),
            )
            document = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("[VG_PRIVATE_PERSON_1]", document)
            self.assertIn("[VG_PRIVATE_PHONE_1]", document)
            self.assertEqual(archive.testzip(), None)

    def test_docx_non_content_metadata_is_removed_without_blocking(self):
        source = self.make_docx(
            collaboration_metadata=True,
            digital_signature=True,
        )
        target = self.root / "客户资料-安全版.docx"
        result = process_document(source, target)
        self.assertIn(
            "已从安全版移除 Office 数字签名；安全副本改写后原签名本就会失效。",
            result.warnings,
        )
        self.assertIn("已从安全版移除协作者身份和扩展评论元数据。", result.warnings)
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            self.assertFalse(any(name.startswith("_xmlsignatures/") for name in names))
            self.assertNotIn("word/people.xml", names)
            self.assertNotIn("word/commentsExtended.xml", names)
            content_types = archive.read("[Content_Types].xml").decode("utf-8")
            root_rels = archive.read("_rels/.rels").decode("utf-8")
            document_rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
            self.assertNotIn("digital-signature", content_types + root_rels)
            self.assertNotIn("people.xml", content_types + document_rels)
            self.assertNotIn("commentsExtended", content_types + document_rels)
            self.assertEqual(archive.testzip(), None)

    def test_docx_document_variables_are_detected_and_removed(self):
        source = self.make_docx(document_variables=True)
        target = self.root / "客户资料-安全版.docx"
        result = process_document(source, target)
        self.assertIn("已从安全版移除文档变量。", result.warnings)
        originals = {mapping.original for mapping in result.mappings}
        self.assertIn("13600002222", originals)
        with zipfile.ZipFile(target) as archive:
            settings = archive.read("word/settings.xml").decode("utf-8")
            self.assertNotIn("docVars", settings)
            self.assertNotIn("13600002222", settings)
            self.assertIn('w:percent="100"', settings)
            self.assertEqual(archive.testzip(), None)
        with zipfile.ZipFile(source) as archive:
            self.assertIn(
                "13600002222",
                archive.read("word/settings.xml").decode("utf-8"),
            )

    def test_office_media_fails_closed_without_ocr(self):
        source = self.make_docx()
        with zipfile.ZipFile(source, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/media/image1.png", b"not-a-real-image")
        with self.assertRaisesRegex(DocumentProcessingError, "包含图片"):
            process_document(source, self.root / "安全版.docx")

    def test_office_duplicate_paths_fail_closed(self):
        source = self.make_docx()
        with zipfile.ZipFile(source, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", b"duplicate")
        with self.assertRaisesRegex(DocumentProcessingError, "重复内部路径"):
            process_document(source, self.root / "安全版.docx")

    def test_xlsx_sensitive_formula_fails_closed(self):
        source = self.make_xlsx(formula="13800138000")
        with self.assertRaisesRegex(DocumentProcessingError, "公式中包含敏感信息"):
            process_document(source, self.root / "安全版.xlsx")

    def test_xlsx_split_formula_literal_fails_closed(self):
        source = self.make_xlsx(formula='&quot;138&quot;&amp;&quot;00138000&quot;')
        with self.assertRaisesRegex(DocumentProcessingError, "公式中包含敏感信息"):
            process_document(source, self.root / "安全版.xlsx")

    def test_xlsx_numeric_phone_is_converted_to_safe_inline_string(self):
        source = self.make_xlsx(numeric_phone=True)
        target = self.root / "数值手机号-安全版.xlsx"
        process_document(source, target)
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertNotIn("13800138000", xml)
        self.assertIn('t="inlineStr"', xml)
        self.assertIn("[VG_PRIVATE_PHONE_1]", xml)

    def test_xlsx_preserves_workbook_and_masks_inline_strings(self):
        source = self.make_xlsx()
        target = self.root / "客户资料-安全版.xlsx"
        result = process_document(source, target)
        self.assertEqual(result.output_format, "xlsx")
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("[VG_PRIVATE_PERSON_1]", xml)
            self.assertIn("[VG_PRIVATE_PHONE_1]", xml)
            self.assertNotIn("13800138000", xml)
            self.assertEqual(archive.testzip(), None)

    def test_xlsx_field_header_style_tab_name_is_not_treated_as_person(self):
        tab_name = "姓名\t日期\t星期\t签到时间\t签退时间"
        source = self.make_xlsx(sheet_name=tab_name)
        target = self.root / "字段表头Tab-安全版.xlsx"

        result = process_document(source, target)

        self.assertEqual(result.output_format, "xlsx")
        with zipfile.ZipFile(target) as archive:
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
        self.assertIn("姓名&#9;日期&#9;星期&#9;签到时间&#9;签退时间", workbook)

    def test_xlsx_generic_and_month_tab_names_are_not_structural_pii(self):
        for index, tab_name in enumerate(("Sheet1", "2024年12月"), start=1):
            source = self.make_xlsx(sheet_name=tab_name)
            target = self.root / f"普通Tab-{index}-安全版.xlsx"
            result = process_document(source, target)
            self.assertEqual(result.output_format, "xlsx")

    def test_xlsx_full_row_defined_name_reference_is_not_a_phone(self):
        source = self.make_xlsx(
            defined_name=("全表范围", "客户!$1:$1048576"),
        )
        target = self.root / "整表范围-安全版.xlsx"

        result = process_document(source, target)

        self.assertEqual(result.output_format, "xlsx")
        with zipfile.ZipFile(target) as archive:
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
        self.assertIn("客户!$1:$1048576", workbook)

    def test_xlsx_defined_name_real_secret_still_fails_closed(self):
        source = self.make_xlsx(
            defined_name=("联系电话", "&quot;13800138000&quot;"),
        )
        with self.assertRaisesRegex(
            DocumentProcessingError,
            "定义名称的公式内容包含敏感信息",
        ):
            process_document(source, self.root / "定义名称-安全版.xlsx")

    def test_pdf_extracts_text_and_generates_safe_markdown(self):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import (
                DecodedStreamObject,
                DictionaryObject,
                NameObject,
            )
        except ImportError:
            self.skipTest("pypdf is not installed")
        source = self.root / "联系人张伟.pdf"
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        resources = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 72 720 Td (Phone: 13800138000) Tj ET")
        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = writer._add_object(stream)
        with source.open("wb") as output:
            writer.write(output)

        target = self.root / "客户资料-安全版.md"
        result = process_document(source, target)
        self.assertEqual(result.output_format, "markdown")
        content = target.read_text(encoding="utf-8")
        self.assertIn("[VG_PRIVATE_PHONE_1]", content)
        self.assertNotIn("13800138000", content)
        self.assertNotIn("张伟", content)
        self.assertIn("第 1 页", content)


if __name__ == "__main__":
    unittest.main()
