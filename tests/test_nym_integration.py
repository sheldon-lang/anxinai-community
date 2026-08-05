import os
import unittest

from veilgate_agent._hybrid import HybridMasker
from veilgate_agent._nym import NymEntity
from veilgate_agent._nym import NymRuntime


class HybridLengthRegressionTests(unittest.TestCase):
    def test_nym_is_not_skipped_for_text_over_30000_characters(self):
        class FakeRuntime:
            model_directory = "fake"

            def detect(self, text):
                start = text.rfind("张伟")
                return (
                    NymEntity(
                        type="person",
                        label="姓名",
                        start=start,
                        end=start + 2,
                        value="张伟",
                        confidence=0.99,
                    ),
                )

        masker = HybridMasker(enable_nym=False)
        masker.nym = FakeRuntime()
        masker.nym_error = None
        result = masker.mask("无" * 30_001 + "张伟")
        self.assertIn("[VG_PRIVATE_PERSON_1]", result.text)
        self.assertNotIn("张伟", result.text)


@unittest.skipUnless(
    os.environ.get("ANXINAI_RUN_NYM_TESTS") == "1",
    "set ANXINAI_RUN_NYM_TESTS=1 to run the 115MB model integration test",
)
class NymIntegrationTests(unittest.TestCase):
    def test_chinese_semantic_entities(self):
        runtime = NymRuntime()
        entities = runtime.detect(
            "张伟负责北京星河科技有限公司的合同，"
            "地址北京市朝阳区建国路88号。"
        )
        values = {(entity.type, entity.value) for entity in entities}
        self.assertIn(("person", "张伟"), values)
        self.assertIn(("company_name", "北京星河科技有限公司"), values)
        self.assertIn(("address", "北京市朝阳区建国路88号"), values)
