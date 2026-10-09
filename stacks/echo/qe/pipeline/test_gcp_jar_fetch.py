# test_gcp_jar_fetch.py
#
# fetch-gcp-jars.sh and glue.tf's local.gcp_glue_jars are two hand-maintained
# copies of one fact. This asserts they agree.
#
# WHY THE SCRIPT EXISTS AT ALL. glue.tf has a null_resource that curls each JAR
# into infrastructure/terraform/.cache/jars/, and an aws_s3_object whose
# `source` reads that path. The provider reads the local file at apply time; the
# null_resource runs only when its triggers change. On an ephemeral runner those
# two facts are incompatible: once the null_resource is in state, a later run on
# a FRESH runner sees unchanged triggers, skips the download, and the apply dies
#
#   opening S3 object source (./.cache/jars/iceberg-gcp-bundle-1.6.1.jar):
#   no such file or directory
#
# It broke the uif-echo-uat converge on run 36327029234 -- round 2 created the
# null_resources, round 3 got a new runner -- and would have broken every run
# after, because re-applying cannot make Terraform re-run a resource whose
# triggers have not moved. The script puts the file there before Terraform
# looks; the local-exec's own `if [ ! -f "$dest" ]` guard then makes the
# null_resource a no-op that stays in state unchanged.
#
# WHAT DRIFT WOULD DO. If glue.tf pins a new version and the script does not,
# the script downloads the old JAR, the S3 object uploads it under the NEW
# key... no -- worse. The key comes from glue.tf's filename, and the source path
# comes from glue.tf's filename too, so the provider looks for a file the script
# never fetched and the apply fails the same way it did on 36327029234. If the
# script pins a version glue.tf does not, it downloads a file nothing uploads
# and the failure is silent waste. Either direction is caught here.
#
# THE JARS ARE NOT OPTIONAL AND NOT ONLY FOR GCP. generic_bronze_ingest -- the
# Bronze job every pipeline runs -- passes local.gcp_glue_extra_jars as
# --extra-jars (glue.tf:51). The tempting fix of skipping them outside dev would
# leave that job pointing at S3 keys that do not exist, and Glue accepts any
# string for --extra-jars, so it would apply clean and fail at run time. A case
# below pins that, so the gate cannot be added later without an argument.
import os
import re
import unittest


HERE = os.path.dirname(__file__)
SCRIPT = os.path.join(HERE, "..", "..", "scripts", "fetch-gcp-jars.sh")
GLUE_TF = os.path.join(HERE, "..", "..", "infrastructure", "terraform", "glue.tf")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def script_jars():
    """{filename: url} from the script's JARS array."""
    m = re.search(r"^JARS=\((.*?)^\)", read(SCRIPT), re.S | re.M)
    assert m, "could not find the JARS array in fetch-gcp-jars.sh"
    out = {}
    for line in m.group(1).splitlines():
        line = line.strip().strip('"')
        if not line or line.startswith("#"):
            continue
        name, _, url = line.partition("|")
        out[name] = url
    return out


def terraform_jars():
    """{filename: url} from local.gcp_glue_jars."""
    src = read(GLUE_TF)
    i = src.index("gcp_glue_jars = {")
    block = src[i:src.index("\n  }", i)]
    out = {}
    for m in re.finditer(
        r'filename\s*=\s*"([^"]+)"\s*\n\s*url\s*=\s*"([^"]+)"', block
    ):
        out[m.group(1)] = m.group(2)
    # The block lists version before filename before url; the pairing above
    # relies on filename immediately preceding url, which it does. If that
    # order changes this returns fewer entries and the guard below fires.
    return out


class TestTheTwoListsAgree(unittest.TestCase):
    def test_both_parsers_find_something(self):
        # Guards both regexes. With either broken, the comparison below passes
        # against an empty dict on both sides.
        self.assertEqual(2, len(script_jars()), "expected 2 JARs in the script")
        self.assertEqual(
            2, len(terraform_jars()),
            "expected 2 JARs in local.gcp_glue_jars; the block's field order "
            "may have changed",
        )

    def test_the_filenames_match(self):
        self.assertEqual(
            sorted(terraform_jars()), sorted(script_jars()),
            "glue.tf and fetch-gcp-jars.sh disagree on which JARs exist. The "
            "aws_s3_object's source path comes from glue.tf, so a file the "
            "script does not fetch fails the apply with 'no such file or "
            "directory' -- the exact failure the script was written to stop.",
        )

    def test_the_urls_match(self):
        tf, sh = terraform_jars(), script_jars()
        for name in sorted(set(tf) & set(sh)):
            with self.subTest(jar=name):
                self.assertEqual(
                    tf[name], sh[name],
                    "same filename, different URL -- one of these is fetching "
                    "the wrong artifact under the right name, which no checksum "
                    "here would catch either",
                )

    def test_the_version_in_each_filename_matches_its_url(self):
        # A pinned version appears in three places per JAR: the `version`
        # trigger, the filename and the URL path. The trigger is what makes
        # Terraform re-download, so a filename bumped without the trigger
        # silently keeps the old JAR.
        src = read(GLUE_TF)
        i = src.index("gcp_glue_jars = {")
        block = src[i:src.index("\n  }", i)]
        entries = re.findall(
            r'version\s*=\s*"([^"]+)"\s*\n\s*filename\s*=\s*"([^"]+)"\s*\n\s*url\s*=\s*"([^"]+)"',
            block,
        )
        self.assertEqual(2, len(entries), "expected 2 fully-formed JAR entries")
        for version, filename, url in entries:
            with self.subTest(jar=filename):
                self.assertIn(version, filename)
                self.assertIn(version, url)


class TestTheJarsAreNotGatedOff(unittest.TestCase):
    def test_bronze_ingest_consumes_them(self):
        # The reason a per-environment gate is wrong. If this stops being true,
        # gating becomes arguable -- but it should be argued, not discovered.
        src = read(GLUE_TF)
        i = src.index('resource "aws_glue_job" "generic_bronze_ingest"')
        body = src[i:i + 4000]
        self.assertIn(
            "local.gcp_glue_extra_jars", body,
            "generic_bronze_ingest no longer passes the JARs, so this file's "
            "argument against gating them per environment needs revisiting",
        )

    def test_the_jar_map_has_no_count_or_conditional(self):
        src = read(GLUE_TF)
        i = src.index("gcp_glue_jars = {")
        head = src[max(0, i - 200):i]
        self.assertNotRegex(
            head, r"\?\s*\{",
            "local.gcp_glue_jars has become conditional. Bronze ingest passes "
            "these as --extra-jars and Glue accepts any string for it, so a "
            "gated-off JAR applies clean and fails when a job runs.",
        )


class TestTheScriptIsSafeToCallRepeatedly(unittest.TestCase):
    def test_it_skips_a_file_that_is_already_there(self):
        # It runs before every plan and every apply, including on a developer's
        # machine. Re-downloading 87MB each time would be noticed; silently
        # accepting a truncated file would not, which is why the guard is on
        # -s rather than -f.
        src = read(SCRIPT)
        self.assertRegex(
            src, r'if \[ -s "\$dest" \]',
            "the cache check must use -s, not -f: an interrupted curl leaves a "
            "short file that -f accepts and Glue rejects at run time",
        )

    def test_it_downloads_to_a_temp_file_first(self):
        src = read(SCRIPT)
        self.assertIn('tmp="${dest}.partial"', src)
        self.assertRegex(
            src, r'mv "\$tmp" "\$dest"',
            "a partial download must not land on the real path, or the -s "
            "check above would accept it on the next run",
        )

    def test_it_fails_loudly_on_an_empty_download(self):
        src = read(SCRIPT)
        self.assertRegex(src, r'\[ -s "\$tmp" \] \|\|')
        self.assertIn("set -euo pipefail", src)


if __name__ == "__main__":
    unittest.main()
