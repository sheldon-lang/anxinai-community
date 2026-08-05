import unittest

from veilgate_agent import DecodeOptions, OPF, RedactionResult, redact


class OPFCompatibilityTests(unittest.TestCase):
    def test_structured_result_matches_schema(self):
        text = "联系人张伟，手机号13800138000，邮箱zhangwei@example.com"
        result = OPF().redact(text)

        self.assertIsInstance(result, RedactionResult)
        self.assertEqual(result.schema_version, 1)
        self.assertEqual(result.summary["output_mode"], "typed")
        self.assertEqual(result.summary["span_count"], 3)
        self.assertEqual(
            [span.label for span in result.detected_spans],
            ["private_person", "private_phone", "private_email"],
        )
        self.assertNotIn("张伟", result.redacted_text)
        self.assertNotIn("13800138000", result.redacted_text)

    def test_module_redact_returns_text(self):
        self.assertEqual(redact("手机号13800138000"), "手机号")

    def test_redacted_output_mode_collapses_labels(self):
        result = OPF(output_mode="redacted").redact("邮箱a@example.com")
        self.assertEqual(result.detected_spans[0].label, "redacted")
        self.assertEqual(result.summary["by_label"], {"redacted": 1})

    def test_fluent_configuration_and_decode_override(self):
        filter_ = (
            OPF()
            .set_device(device="cpu")
            .set_output_mode("typed")
            .set_decode_mode("argmax")
            .trim_whitespace()
            .output_text_only()
        )
        self.assertEqual(filter_.redact("电话13800138000"), "电话")
        resolved = filter_.resolve_decode_options(
            decode=DecodeOptions(decode_mode="viterbi")
        )
        self.assertEqual(resolved.decode_mode, "viterbi")

    def test_cn_id_checksum(self):
        result = OPF().redact("身份证11010519491231002X")
        self.assertEqual(result.detected_spans[0].label, "account_number")


class VeilGateExtensionsTests(unittest.TestCase):
    def test_mask_and_restore(self):
        source = "联系人张伟，手机号13800138000"
        filter_ = OPF()
        masked = filter_.mask(source)

        self.assertEqual(
            masked.text,
            "联系人[VG_PRIVATE_PERSON_1]，手机号[VG_PRIVATE_PHONE_1]",
        )
        self.assertEqual(filter_.restore(masked.text, masked), source)


if __name__ == "__main__":
    unittest.main()

