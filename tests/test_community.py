from pathlib import Path
import os
import tempfile
import unittest

from veilgate_agent._workbuddy import (
    create_review,
    decide_review,
    public_summary,
)
from veilgate_agent.settings_server import settings_html
from veilgate_agent.workbuddy_cli import parser


class CommunityBoundaryTests(unittest.TestCase):
    def test_settings_have_no_activation_or_payment(self) -> None:
        html = settings_html("local-token")
        self.assertIn("社区版", html)
        self.assertNotIn("激活码", html)
        self.assertIn("支付", html)  # Explicitly states that payment logic is absent.

    def test_cli_excludes_commercial_gateway_and_directory_hook(self) -> None:
        help_text = parser().format_help()
        self.assertNotIn("gateway", help_text)
        self.assertNotIn("directory", help_text)
        self.assertIn("scan", help_text)
        self.assertIn("restore", help_text)

    def test_local_review_only_releases_safe_copy_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            previous_home = os.environ.get("ANXINAI_HOME")
            previous_disable_nym = os.environ.get("ANXINAI_DISABLE_NYM")
            os.environ["ANXINAI_HOME"] = temporary
            os.environ["ANXINAI_DISABLE_NYM"] = "1"
            try:
                source = Path(temporary) / "客户名单.csv"
                source.write_text(
                    "姓名,手机号\n联系人张伟,13800138000\n",
                    encoding="utf-8",
                )
                pending = create_review(source)
                pending_summary = public_summary(pending).to_dict()
                self.assertEqual(pending_summary["status"], "pending")
                self.assertNotIn("safe_path", pending_summary)

                approved = decide_review(
                    str(pending["ticket_id"]),
                    str(pending["access_token"]),
                    "approved",
                )
                approved_summary = public_summary(approved).to_dict()
                self.assertEqual(approved_summary["status"], "approved")
                safe_path = Path(str(approved_summary["safe_path"]))
                self.assertTrue(safe_path.is_file())
                safe_text = safe_path.read_text(encoding="utf-8")
                self.assertNotIn("13800138000", safe_text)
                self.assertNotIn("张伟", safe_text)
            finally:
                if previous_home is None:
                    os.environ.pop("ANXINAI_HOME", None)
                else:
                    os.environ["ANXINAI_HOME"] = previous_home
                if previous_disable_nym is None:
                    os.environ.pop("ANXINAI_DISABLE_NYM", None)
                else:
                    os.environ["ANXINAI_DISABLE_NYM"] = previous_disable_nym
