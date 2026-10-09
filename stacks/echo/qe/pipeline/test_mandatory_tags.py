# test_mandatory_tags.py
#
# Pins the Pearson mandatory resource tags: the seven `t_` keys plus `Project`,
# applied through provider default_tags, with a value in every environment.
#
# WHY A TEST. default_tags is invisible at the point of use -- no resource
# declaration mentions it -- so the three ways it silently stops working all
# look like nothing:
#
#   1. A new `provider "aws"` block with an alias and no default_tags block.
#      An aliased provider does NOT inherit the default provider's, so its
#      resources come out untagged. This root already has one alias (aws.use1,
#      for the CLOUDFRONT-scope WAF), and those are exactly the resources a
#      compliance programme audits.
#   2. A key dropped from local.default_tags. Nothing references the map by
#      name, so removing an entry breaks no plan.
#   3. A new environment's tfvars without the values. The variables have no
#      defaults on purpose, so this fails at plan time rather than silently --
#      but it fails for whoever runs the plan, which for uat and prod is a
#      person mid-deploy, not CI. Failing here is cheaper.
#
# The values themselves are deliberately NOT asserted, except t_dcl. An app id
# or a cost centre changing is a service-record fact this test should not
# arbitrate. t_dcl is asserted because it is a classification decision with
# controls keyed off it, recorded as 3 by owner decision on 2026-09-23, and
# because understating it is the dangerous direction: it exempts protected data
# from controls it is required to have.
import os
import re
import unittest


INFRA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "infrastructure", "terraform"
)
ENVS_DIR = os.path.join(INFRA_DIR, "envs")

# The seven Pearson keys. Case matters -- AWS tag keys are case-sensitive, and
# the AWS key is `t_AppID` while the Terraform variable is `t_app_id`.
PEARSON_KEYS = (
    "t_name",
    "t_AppID",
    "t_environment",
    "t_cost_centre",
    "t_dcl",
    "t_owner_individual",
    "t_responsible_individuals",
)

# Set in each env's tfvars. t_name has a default ("echo") and t_environment is
# derived from `environment`, so neither is supplied per environment.
TFVARS_KEYS = (
    "t_app_id",
    "t_cost_centre",
    "t_dcl",
    "t_owner_individual",
    "t_responsible_individuals",
)


def _read(name):
    with open(os.path.join(INFRA_DIR, name), encoding="utf-8") as f:
        return f.read()


class TestDefaultTagsMap(unittest.TestCase):
    def setUp(self):
        self.locals_tf = _read("locals.tf")
        # The map is a ternary -- `local.is_uif_stack ? { ... } : {}` -- so the
        # legacy stacks get an empty map and no tags. Matching the ternary form
        # rather than a bare `{` is deliberate: if someone removes the gate,
        # this stops matching and every assertion in this class fails loudly,
        # rather than passing against an ungated map.
        m = re.search(
            r"default_tags = local\.is_uif_stack \? \{(.*?)\n  \} : \{\}",
            self.locals_tf,
            re.S,
        )
        self.assertIsNotNone(
            m, "local.default_tags is gone, or no longer gated on is_uif_stack"
        )
        self.block = m.group(1)

    def test_every_pearson_key_is_present(self):
        for key in PEARSON_KEYS:
            with self.subTest(key=key):
                self.assertRegex(
                    self.block,
                    rf"(?m)^\s+{re.escape(key)}\s+=",
                    f"{key} is not in local.default_tags. Nothing references "
                    f"that map by name, so dropping a key breaks no plan and "
                    f"shows up only as an untagged resource in an audit.",
                )

    def test_project_survives_alongside_them(self):
        # Not a Pearson key, kept deliberately: resources in this root already
        # set it, and the control panel's GET /connections discovers secrets by
        # the tag app=echo. It tracks local.project_tag rather than a literal,
        # so prod keeps "eds" through its project_tag_override.
        self.assertRegex(self.block, r"(?m)^\s+Project\s+=\s+local\.project_tag")

    def test_uat_maps_to_stg(self):
        # Pearson's standard offers Dev, STG and Prod with no UAT value, so STG
        # is the only non-dev non-prod token available. Asserted so the mapping
        # is not "corrected" to "UAT" by someone who assumes it was a typo.
        self.assertRegex(self.locals_tf, r'uat\s+=\s+"STG"')

    def test_t_environment_is_derived_not_supplied(self):
        self.assertRegex(self.block, r"(?m)^\s+t_environment\s+=\s+local\.t_environment")


class TestProvidersCarryTheTags(unittest.TestCase):
    def test_every_aws_provider_block_has_default_tags(self):
        # The failure this exists for: an aliased provider added without a
        # default_tags block, whose resources are then the only untagged ones.
        providers_tf = _read("providers.tf")
        blocks = re.findall(r'provider\s+"aws"\s*\{(.*?)\n\}', providers_tf, re.S)
        self.assertGreaterEqual(len(blocks), 2, "expected at least the default and use1 providers")
        for i, body in enumerate(blocks):
            alias = re.search(r'alias\s+=\s+"(\w+)"', body)
            with self.subTest(provider=alias.group(1) if alias else "default"):
                self.assertIn(
                    "default_tags",
                    body,
                    "an aws provider block has no default_tags; an aliased "
                    "provider does not inherit the default one's, so its "
                    "resources would be the only untagged resources here.",
                )
                self.assertIn("local.default_tags", body)


class TestOnlyTheUifStacksAreTagged(unittest.TestCase):
    """The tags are for uif-echo-* only -- owner decision of 2026-09-23.

    Tagging the live echo-dev-*, echo-uat-* and eds-prod-* resources was never
    asked for, and those three retire on the coexist-then-retire schedule. The
    gate is what enforces that; these assertions are what stop it eroding from
    either end -- a legacy env quietly gaining values that the gate then
    ignores (so someone "fixes" the gate), or a uif env losing them.
    """

    # uif stacks, by their name_prefix_override, not by filename.
    UIF = ("dev-uif", "uat-uif")
    LEGACY = ("dev", "uat", "prod")

    def _envs(self):
        return sorted(
            f[: -len(".tfvars")] for f in os.listdir(ENVS_DIR) if f.endswith(".tfvars")
        )

    def _text(self, env):
        with open(os.path.join(ENVS_DIR, f"{env}.tfvars"), encoding="utf-8") as f:
            return f.read()

    def test_all_five_environments_exist(self):
        # dev and uat each carry two stacks during the migration: the legacy
        # one and the uif- one beside it.
        self.assertEqual(self._envs(), sorted(self.UIF + self.LEGACY))

    def test_the_map_is_gated_on_is_uif_stack(self):
        # The whole mechanism. Without the ternary every environment gets the
        # tags, which is what this was corrected back from.
        self.assertRegex(
            _read("locals.tf"),
            r"default_tags = local\.is_uif_stack \?",
            "local.default_tags is no longer gated on is_uif_stack, so the "
            "legacy stacks would be tagged on their next apply.",
        )

    def test_uif_environments_set_every_value(self):
        for env in self.UIF:
            text = self._text(env)
            for key in TFVARS_KEYS:
                with self.subTest(env=env, key=key):
                    self.assertRegex(
                        text,
                        rf"(?m)^{re.escape(key)}\s+=",
                        f"{env}.tfvars does not set {key}. s3.tf's "
                        f"release_artifacts precondition fails the plan for a "
                        f"uif stack with any of the five empty.",
                    )

    def test_legacy_environments_set_none_of_them(self):
        for env in self.LEGACY:
            text = self._text(env)
            for key in TFVARS_KEYS:
                with self.subTest(env=env, key=key):
                    self.assertNotRegex(
                        text,
                        rf"(?m)^{re.escape(key)}\s+=",
                        f"{env}.tfvars sets {key}, but this is a legacy stack "
                        f"and the gate means the value has no effect. A value "
                        f"that looks live and is not invites someone to "
                        f"'fix' the gate.",
                    )

    def test_dcl_is_three_on_both_uif_stacks(self):
        # A classification is a property of the data the stack holds, so the
        # same data flowing through dev and uat means the same level; a
        # difference would mean one of them is wrong.
        for env in self.UIF:
            with self.subTest(env=env):
                self.assertRegex(self._text(env), r'(?m)^t_dcl\s+=\s+"3"')


class TestTheGateCannotLeaveAUifStackBlank(unittest.TestCase):
    def test_the_release_bucket_asserts_all_five(self):
        # Where the strictness went. A variable validation cannot see
        # is_uif_stack, so it cannot demand a value on a uif stack only; the
        # release_artifacts bucket exists only on a uif stack, so a
        # precondition on it fires exactly there. Verified on 2026-09-23 by
        # planning dev-uif with `-var 't_dcl='`, which failed with "Resource
        # precondition failed".
        s3_tf = _read("s3.tf")
        for key in TFVARS_KEYS:
            with self.subTest(key=key):
                self.assertRegex(
                    s3_tf,
                    rf"var\.{re.escape(key)} != \"\"",
                    f"s3.tf's release_artifacts precondition no longer checks "
                    f"{key}; the relaxed validation would let a uif stack "
                    f"apply with it blank.",
                )


class TestS3ObjectsStayUnderTheTagLimit(unittest.TestCase):
    """An S3 OBJECT accepts ten tags. Not fifty. Ten.

    THE FAILURE. The first uif-echo-uat apply (run 36253830424) created 86 of
    210 resources and then rejected two jar uploads outright:

        api error BadRequest: Object tags cannot be greater than 10

    local.default_tags contributes 8 keys on a uif stack. aws_s3_object
    "gcp_glue_jars" added Project, Environment, ManagedBy and Purpose; Project
    overlapped, so the effective set was 11.

    WHY IT NEEDS A TEST RATHER THAN CARE. Nothing warns you. The count is
    validated by S3 at PutObject and by nothing before it -- `terraform
    validate` passes, the plan is clean and prints all 11 tags as an
    ordinary-looking diff, and each tag is individually correct and mandated.
    The limit is the surprise: 10 is specific to S3 objects where almost every
    other AWS resource allows 50, so 11 looks harmless to anyone who has
    internalised the usual cap.

    The legacy stacks could never have caught it either -- default_tags is gated
    on is_uif_stack, so echo-dev and echo-uat contribute 0 and only ever saw 4.

    The assertion is on the SUM, because that is what breaks. Either side can
    grow innocently -- an eighth Pearson key, or one more per-object tag -- and
    neither edit looks like it concerns the other.
    """

    LIMIT = 10
    ROOTS = ("terraform", "terraform-gold")

    def _default_tag_keys(self):
        m = re.search(
            r"default_tags = local\.is_uif_stack \? \{(.*?)\n  \} : \{\}",
            _read("locals.tf"), re.S,
        )
        self.assertIsNotNone(m, "local.default_tags is gone or no longer gated")
        return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", m.group(1), re.M))

    def _s3_objects(self):
        """(file, resource name, own tag keys) for every aws_s3_object."""
        base = os.path.dirname(INFRA_DIR)
        out = []
        for root in self.ROOTS:
            d = os.path.join(base, root)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if not name.endswith(".tf"):
                    continue
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    src = f.read()
                for m in re.finditer(
                    r'resource "aws_s3_object" "([a-z0-9_]+)" \{(.*?)\n\}', src, re.S
                ):
                    t = re.search(r"tags = \{(.*?)\n  \}", m.group(2), re.S)
                    keys = set(
                        re.findall(
                            r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", t.group(1), re.M
                        )
                    ) if t else set()
                    out.append(("%s/%s" % (root, name), m.group(1), keys))
        return out

    def test_the_default_tag_count_is_what_we_think(self):
        # Guards the arithmetic below. If default_tags came back empty because
        # the regex stopped matching, every sum would pass trivially.
        keys = self._default_tag_keys()
        self.assertGreaterEqual(
            len(keys), 8,
            "expected the 7 Pearson keys plus Project in default_tags; found "
            "%s" % sorted(keys),
        )

    def test_there_are_s3_objects_to_check(self):
        self.assertGreaterEqual(
            len(self._s3_objects()), 20,
            "expected the roots to declare many aws_s3_object resources; the "
            "parser may have stopped matching",
        )

    def test_no_s3_object_exceeds_ten_effective_tags(self):
        defaults = self._default_tag_keys()
        over = []
        for fname, res, own in self._s3_objects():
            total = len(defaults | own)
            if total > self.LIMIT:
                over.append(
                    "%s (%s): %d effective tags = %d default + %d own, "
                    "overlapping %s"
                    % (res, fname, total, len(defaults), len(own),
                       sorted(defaults & own) or "nothing")
                )
        self.assertEqual(
            [], over,
            "S3 rejects PutObject above %d tags with 'Object tags cannot be "
            "greater than %d', and the plan does not warn: %s"
            % (self.LIMIT, self.LIMIT, "; ".join(over)),
        )


if __name__ == "__main__":
    unittest.main()
