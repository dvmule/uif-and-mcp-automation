# test_consumers_are_wired.py
# A shared package is only shared if every consumer actually carries it: the
# module import works in tests (conftest) and the deployed zip contains the
# package (workflow copy / archive_file). Each of these breaks at run time
# only, as an ImportError in a Lambda, so they are pinned here.

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


class BeaconCarriesThePackage(unittest.TestCase):
    WORKFLOW = _read(".github/workflows/deploy-beacon-dev.yml")

    def test_both_lambda_zips_copy_it(self):
        # the export Lambdas zip AND the API zip: both import beacon_export,
        # which imports pearson_notifications
        self.assertEqual(self.WORKFLOW.count(
            'cp -r "$GITHUB_WORKSPACE/platform/python/pearson_notifications" "$build/"'), 2)

    def test_a_package_change_redeploys_and_is_tested(self):
        self.assertIn('- "platform/python/**"', self.WORKFLOW)

    def test_tests_can_import_it(self):
        self.assertIn('/ "platform" / "python"', _read("stacks/beacon/qe/conftest.py"))

    def test_beacon_no_longer_owns_the_transport(self):
        src = _read("stacks/beacon/src/apps/beacon_export/email_delivery.py")
        self.assertIn("from pearson_notifications import NotificationsClient", src)
        for gone in ("def _mint_token", "def _post(", "urllib.request", "_token_cache["):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, src)


class EchoGoldCarriesThePackage(unittest.TestCase):
    ALERTS_TF = _read("stacks/echo/infrastructure/terraform-gold/alerts.tf")
    WORKFLOW = _read(".github/workflows/deploy-echo-gold-dev.yml")

    def test_the_notifier_zip_holds_the_package_as_a_directory(self):
        archive = re.search(r'data "archive_file" "gold_alert_notify".*?\n\}', self.ALERTS_TF, re.S)
        self.assertIsNotNone(archive)
        for name in ("pearson_notifications/__init__.py", "pearson_notifications/client.py",
                     "gold_alert_notify.py", "gold_resources.py"):
            with self.subTest(file=name):
                self.assertIn(f'filename = "{name}"', archive.group(0))

    def test_a_package_change_redeploys_and_is_tested(self):
        self.assertIn('- "platform/python/**"', self.WORKFLOW)
        self.assertIn('- "stacks/echo/src/apps/lambdas/gold_alert_notify.py"', self.WORKFLOW)
        # the shared package's own tests live in THIS suite (see
        # test_pearson_notifications.py for why not under platform/)
        self.assertIn("python -m pytest stacks/echo/qe/gold -q", self.WORKFLOW)

    def test_tests_can_import_it(self):
        self.assertIn('/ "platform" / "python"', _read("stacks/echo/qe/conftest.py"))


if __name__ == "__main__":
    unittest.main()
