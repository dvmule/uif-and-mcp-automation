# test_ui_bundle_publish.py
#
# Pins the publish-a-restored-bundle path in uif-echo-ui-deploy.sh.
#
# WHY A TEST. A promote must not rebuild the UI: the bundle's content hash IS
# its identity, and `npm run build` is not byte-reproducible across runners, so
# rebuilding would publish bytes no release tag names. The guard is one
# conditional, and nothing about the surrounding code makes its absence
# obvious.
#
# The sync is the other half. `aws s3 sync --delete --exclude "m/*"` is what
# lets this script prune stale Echo assets without deleting beacon's and
# transform's apps from the same bucket. Both the --delete and the exclude must
# survive, together: --delete without the exclude wipes another team's site,
# and the exclude without --delete leaves stale assets forever.
import os
import re
import unittest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "..", "infrastructure", "deploy",
    "uif-echo-ui-deploy.sh",
)


def source():
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


def uncommented():
    return "\n".join(
        l for l in source().splitlines() if not l.strip().startswith("#")
    )


class TestFromBundle(unittest.TestCase):
    def test_bundle_variable_is_read(self):
        self.assertIn("UIF_ECHO_UI_BUNDLE", uncommented())

    def test_build_is_skipped_when_a_bundle_is_given(self):
        src = uncommented()
        # npm must be reachable only inside the else branch.
        self.assertRegex(
            src,
            r'if \[ -n "\$\{UIF_ECHO_UI_BUNDLE:-\}" \];[\s\S]*?else[\s\S]*?npm ci[\s\S]*?npm run build[\s\S]*?fi',
            "npm ci/npm run build must sit in the else branch: a promote that "
            "rebuilds publishes bytes no release tag names",
        )

    def test_a_missing_bundle_directory_is_refused(self):
        self.assertIn("is not a directory", uncommented())


class TestSyncSemanticsSurvive(unittest.TestCase):
    def test_delete_and_exclude_are_both_present_on_one_command(self):
        src = uncommented()
        m = re.search(r"aws s3 sync[\s\S]{0,300}?--region", src)
        self.assertIsNotNone(m, "could not find the sync")
        sync = m.group(0)
        self.assertIn("--delete", sync)
        self.assertIn('--exclude "m/*"', sync)

    def test_there_is_exactly_one_sync(self):
        self.assertEqual(
            1, len(re.findall(r"aws s3 sync", uncommented())),
            "a second sync is how one of them loses the m/* exclude",
        )


if __name__ == "__main__":
    unittest.main()
