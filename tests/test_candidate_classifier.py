import unittest

from veilgate_agent._candidate_classifier import classify_candidate
from veilgate_agent._hybrid import HybridMasker
from veilgate_agent._nym import NymEntity


class CandidateClassifierTests(unittest.TestCase):
    def test_rejects_role_followed_by_actions_as_person_names(self):
        masker = HybridMasker(enable_nym=False, enabled_rules=["person_name"])
        source = (
            "筹备小组负责人重申演练流程、注意事项及纪律要求。"
            "各子公司参演负责人参加。"
        )
        result = masker.mask(source, use_nym=False)
        self.assertEqual(result.text, source)
        self.assertEqual(result.findings, ())

    def test_accepts_explicit_person_field(self):
        masker = HybridMasker(enable_nym=False, enabled_rules=["person_name"])
        result = masker.mask("负责人：张伟", use_nym=False)
        self.assertNotIn("张伟", result.text)

    def test_nym_person_requires_high_confidence(self):
        self.assertFalse(
            classify_candidate("person", "张伟", confidence=0.60).accepted
        )
        self.assertTrue(
            classify_candidate("person", "张伟", confidence=0.95).accepted
        )

    def test_nym_organization_requires_a_complete_suffix(self):
        for value in ("金", "出行", "数金", "市民云", "金控云平台"):
            self.assertFalse(
                classify_candidate(
                    "company_name", value, confidence=0.99
                ).accepted
            )
        self.assertTrue(
            classify_candidate(
                "company_name", "交子金控集团", confidence=0.98
            ).accepted
        )

    def test_explicit_ambiguous_organization_is_not_masked(self):
        masker = HybridMasker(
            enable_nym=False,
            enabled_rules=["organization_name"],
        )
        result = masker.mask("单位名称：出行", use_nym=False)
        self.assertEqual(result.text, "单位名称：出行")

    def test_rejects_address_fragment(self):
        self.assertFalse(
            classify_candidate(
                "address", "在天府", confidence=0.90
            ).accepted
        )

    def test_hybrid_filters_low_confidence_nym_candidates(self):
        class FakeRuntime:
            model_directory = "fake"

            def detect(self, text):
                return (
                    NymEntity(
                        type="person",
                        label="姓名",
                        start=text.index("张伟"),
                        end=text.index("张伟") + 2,
                        value="张伟",
                        confidence=0.50,
                    ),
                )

        masker = HybridMasker(enable_nym=False, enabled_rules=["person_name"])
        masker.nym = FakeRuntime()
        result = masker.mask("张伟负责合同")
        self.assertEqual(result.text, "张伟负责合同")


if __name__ == "__main__":
    unittest.main()
