# test_backend_policy_covers_every_root.py
#
# Pins that the Terraform state grant covers EVERY root's state file, not just
# the main root's.
#
# WHY THIS EXISTS, AND WHY IT IS NOT DERIVED LIKE THE REST. Echo's deploy policy
# is rendered from the Terraform by required-actions.py -- a policy derived from
# the thing it authorises stays correct as that thing grows. The backend policy
# cannot work that way: the state bucket is not a resource either root manages,
# so Terraform never mentions it and there is nothing to derive from. It is
# written by hand in bootstrap-environment.sh instead.
#
# That hand-written seam is exactly where this broke. UIFPE-576 folded the gold
# root into the promote workflow and updated required-actions.py to derive the
# deploy policy from BOTH roots -- so the role could create every gold resource.
# It did not update the backend policy, which still granted object access to
# `tfstate/infra-terraform.tfstate` alone. The result was a role that could
# build gold's infrastructure and could not read gold's state, so the promote
# would have failed at `terraform init` on the gold root, before any of the
# permissions it did have came into play.
#
# The failure is also badly shaped for debugging: `init` reports an S3
# AccessDenied on a key, with nothing connecting it to a deploy policy that was
# updated for two roots while its sibling was updated for one.
#
# WHAT IS ASSERTED, AND WHY NOT tfstate/*. A wildcard would cover both roots in
# one line and never need touching again -- and would also silently grant any
# state a future root dropped in this bucket. Each key is named, so adding a
# root is a visible change to this policy rather than an invisible one.
import glob
import json
import os
import re
import unittest


HERE = os.path.dirname(__file__)
INFRA = os.path.join(HERE, "..", "..", "infrastructure")
BOOTSTRAP = os.path.join(INFRA, "deploy", "bootstrap-environment.sh")
POLICY_DIR = os.path.join(HERE, "..", "..", "..", "..", "capabilities", "infrastructure")

# The two roots and the backend-file extension each uses. They differ, which is
# its own small trap: a lookup written for one will silently find nothing for
# the other.
ROOTS = {
    "main": (os.path.join(INFRA, "terraform", "backends"), ".hcl"),
    "gold": (os.path.join(INFRA, "terraform-gold", "backends"), ".s3.tfbackend"),
}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def state_keys(env):
    """Every (bucket, key) this environment's roots declare."""
    out = {}
    for label, (d, ext) in ROOTS.items():
        p = os.path.join(d, env + ext)
        if not os.path.isfile(p):
            continue
        body = read(p)
        bucket = re.search(r'^\s*bucket\s*=\s*"([^"]+)"', body, re.M)
        key = re.search(r'^\s*key\s*=\s*"([^"]+)"', body, re.M)
        if bucket and key:
            out[label] = (bucket.group(1), key.group(1))
    return out


class TestTheGeneratorReadsBothRoots(unittest.TestCase):
    """bootstrap-environment.sh is what writes the document."""

    def test_it_locates_the_gold_backend(self):
        src = read(BOOTSTRAP)
        self.assertIn(
            "terraform-gold", src,
            "the generator must look for the gold root's backend file",
        )
        self.assertIn(
            ".s3.tfbackend", src,
            "gold's backend file extension differs from the main root's .hcl; "
            "a lookup written for one finds nothing for the other",
        )

    def test_it_passes_every_key_to_the_generator(self):
        self.assertRegex(
            read(BOOTSTRAP),
            r'"\$STATE_KEY"\s+"\$GOLD_STATE_KEY"',
            "both keys must reach the document generator",
        )

    def test_it_refuses_two_different_state_buckets(self):
        # The statement grants object access inside one bucket. If the roots
        # ever name different buckets, the policy would grant the wrong one and
        # report success.
        self.assertIn(
            "the two roots name different state buckets", read(BOOTSTRAP),
            "a bucket mismatch must be refused, not silently half-granted",
        )

    def test_it_does_not_widen_to_a_wildcard(self):
        # Comment lines are stripped first. The generator's own comment explains
        # why it does NOT use tfstate/*, so searching the raw source finds the
        # explanation and fails on it -- which is what the first version of this
        # test did.
        code = "\n".join(
            line for line in read(BOOTSTRAP).splitlines()
            if not line.strip().startswith("#")
        )
        self.assertNotIn(
            "tfstate/*", code,
            "a wildcard would also grant any state a future root put in this "
            "bucket; each key is named so adding a root is visible",
        )


class TestTheCommittedDocumentsMatch(unittest.TestCase):
    """Each rendered document must cover its environment's roots."""

    def test_every_backend_policy_covers_all_its_roots(self):
        docs = sorted(glob.glob(
            os.path.join(POLICY_DIR, "uif-echo-*-backend-policy.json")))
        self.assertTrue(docs, "expected at least one backend policy document")

        for doc in docs:
            name = os.path.basename(doc)
            # uif-echo-uat-backend-policy.json -> the tfvars env is uat-uif,
            # because these documents are named for the resource prefix while
            # the backend files are named for the tfvars env.
            m = re.match(r"uif-echo-([a-z]+)-backend-policy\.json$", name)
            self.assertIsNotNone(m, "unexpected document name %s" % name)
            env = m.group(1) + "-uif"

            declared = state_keys(env)
            if not declared:
                continue

            granted = set()
            for s in json.loads(read(doc))["Statement"]:
                if s.get("Sid") != "StateObject":
                    continue
                r = s["Resource"]
                granted.update(r if isinstance(r, list) else [r])

            with self.subTest(document=name):
                for label, (bucket, key) in declared.items():
                    for suffix in ("", ".tflock"):
                        arn = "arn:aws:s3:::%s/%s%s" % (bucket, key, suffix)
                        self.assertIn(
                            arn, granted,
                            "%s does not grant the %s root's state at %s. "
                            "terraform init on that root fails with an S3 "
                            "AccessDenied naming the key, and nothing points "
                            "at this policy as the cause."
                            % (name, label, arn),
                        )


if __name__ == "__main__":
    unittest.main()
