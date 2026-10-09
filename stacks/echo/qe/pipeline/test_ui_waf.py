# test_ui_waf.py
#
# Guards the UI web ACL's OFF switch, because trivy stopped guarding it.
#
# WHY THIS FILE EXISTS. AVD-AWS-0011 ("Distribution does not utilize a WAF")
# was reported against aws_cloudfront_distribution.ui on every run up to
# 88f87890, and reported ZERO on 4ead0d76 -- the commit that added waf.tf with
# var.enable_ui_waf defaulting to false. Nothing was suppressed. The finding
# disappeared because cloudfront.tf now says
#
#     web_acl_id = one(aws_wafv2_web_acl.ui[*].arn)
#
# and trivy cannot evaluate that splat down to null, so it reads the
# distribution as WAF-protected while no web ACL is deployed in any
# environment. A false green on a compliance item, which is strictly worse than
# the red it replaced: the red was accurate.
#
# These assertions are the replacement signal. They check what a text parse can
# actually check -- that the flag is off, that turning it on cannot produce a
# blackhole or reach a live stack, that the wiring which makes the flip work is
# intact, and that nobody quietly adds the suppression trivy does not need.
#
# WHAT THEY CANNOT CHECK is the actual blocker: echo-dev-github-actions holds
# none of the wafv2 permissions waf.tf needs (nine of ten implicitDeny,
# simulated 2026-09-22). No unit test reaches IAM. That gap is why
# test_the_waf_is_live_before_the_compliance_deadline exists rather than an
# assertion about the grant itself -- a date is the only part of "accepted until
# the grant lands" this file can enforce.
#
# Related: infrastructure/deploy/uif-echo-dev-waf-grant.json (the statement to
# add), infrastructure/terraform/variables.tf (enable_ui_waf, the full
# reasoning), docs/uif-echo-uat-rollout-plan.md (correction 9).
import datetime
import glob
import os
import pathlib
import re
import unittest


INFRA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "infrastructure", "terraform"
)
ENVS_DIR = os.path.join(INFRA_DIR, "envs")

# Pearson's account-wide "WAF & IP Sanctions" compliance program, owner
# PCM/SRE/Security, was due 2026-09-30 -- the date apicurio's waf.tf carries.
# Moved to 2026-10-31 on 2026-10-01 by the Echo owner (UIFPE-756) as an
# accepted-overrun window, NOT a new program date: the deploy role now holds
# the wafv2 grant (managed policy uif-echo-dev-waf-deploy, attached
# 2026-10-01) and the ACL's shape was decided (allow by default, block what
# the sanctions and managed rules block), so what remains is the waf.tf
# change and a supervised apply. If this fires again, ship that or move the
# date with its source recorded here, as before.
COMPLIANCE_DEADLINE = datetime.date(2026, 10, 31)


def _read(name):
    with open(os.path.join(INFRA_DIR, name), encoding="utf-8") as f:
        return f.read()


def _tfvars():
    """{env: text} for every environment on disk.

    Derived from the directory rather than listed, for the reason
    src/libs/stack_naming.py gives about the same files: a hardcoded list of
    environments goes stale silently, and the environment it omits is the one
    nobody checks.
    """
    out = {}
    for path in sorted(glob.glob(os.path.join(ENVS_DIR, "*.tfvars"))):
        with open(path, encoding="utf-8") as f:
            out[os.path.splitext(os.path.basename(path))[0]] = f.read()
    return out


def _assignment(text, key):
    """The value assigned to `key`, ignoring commented-out lines.

    Deliberately not a HCL parser -- the same scope stack_naming._parse_tfvars
    keeps. It does have to skip comments, because this file's whole subject is
    a flag whose tfvars entry sits under a fifteen-line comment block that
    mentions the flag by name.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^" + re.escape(key) + r"\s*=\s*(.+?)\s*$", stripped)
        if m:
            return m.group(1)
    return None


class TestTheFlagAndItsGate(unittest.TestCase):
    def test_the_variable_defaults_to_false(self):
        # The default is what protects an environment whose tfvars says
        # nothing -- uat and prod today. A default of true would arm the ACL
        # for them the moment local.is_uif_stack became true for uat.
        variables = _read("variables.tf")
        block = re.search(
            r'variable\s+"enable_ui_waf"\s*\{(.*?)\n\}', variables, re.S
        )
        self.assertIsNotNone(
            block, "variable enable_ui_waf is gone from variables.tf"
        )
        self.assertRegex(block.group(1), r"default\s*=\s*false")

    def test_dev_enables_it(self):
        # Replaces test_no_environment_enables_it_yet (UIFPE-756). That
        # assertion existed because the deploy role had no wafv2 grant, so a
        # true flag armed an apply failure at CreateIPSet. Inverted rather
        # than deleted: the compliance deadline has passed, so what is worth
        # pinning now is that SOME environment enables the ACL.
        value = _assignment(_tfvars()["dev-uif"], "enable_ui_waf")
        self.assertEqual(
            value,
            "true",
            "dev-uif.tfvars no longer enables the UI WAF. The compliance "
            "deadline has passed, so turning it off again needs the "
            "program's agreement, not a tfvars edit.",
        )

    def test_an_enabled_environment_has_a_non_empty_allow_list(self):
        # waf.tf's precondition catches this at plan time; asserting it here
        # fails in seconds instead, and names the environment.
        for env, text in _tfvars().items():
            if _assignment(text, "enable_ui_waf") != "true":
                continue
            with self.subTest(env=env):
                value = (_assignment(text, "ui_allowed_cidrs") or "[]")
                self.assertNotIn(
                    value.replace(" ", ""),
                    ("[]", "null"),
                    f"{env}.tfvars enables the WAF with an empty allow-list. "
                    f"The ACL blocks by default, so that is a blackhole "
                    f"rather than a weak ACL -- it takes the UI dark.",
                )

    def test_enabling_it_requires_a_non_empty_allow_list(self):
        # The web ACL's default_action is block, so an empty allow-list is a
        # total blackhole rather than a weak ACL. waf.tf carries a
        # lifecycle.precondition for exactly this; asserting it here too costs
        # nothing and fails in seconds instead of at plan time.
        for env, text in _tfvars().items():
            if _assignment(text, "enable_ui_waf") != "true":
                continue
            with self.subTest(env=env):
                cidrs = _assignment(text, "ui_allowed_cidrs")
                self.assertNotIn(
                    cidrs,
                    (None, "[]"),
                    f"{env}.tfvars enables the WAF with no ui_allowed_cidrs. "
                    f"default_action is block, so this denies every request "
                    f"to the UI including from whoever runs the apply.",
                )


class TestTheGateCannotBeLoosened(unittest.TestCase):
    def test_both_conditions_are_required(self):
        # AND, not OR. local.is_uif_stack is what keeps the ACL off echo-dev,
        # echo-uat and eds-prod, which are live distributions serving real
        # users -- a block-by-default ACL in front of one is an outage. An OR
        # here, or dropping is_uif_stack, would let a legacy stack arm it from
        # tfvars alone.
        self.assertIn(
            "ui_waf_enabled = local.is_uif_stack && var.enable_ui_waf",
            _read("locals.tf"),
        )

    def test_the_distribution_is_wired_to_the_gated_acl(self):
        # This exact expression is what makes the flip a one-line tfvars
        # change, and `one()` rather than a ternary is deliberate: a
        # conditional does not reliably short-circuit, so
        # aws_wafv2_web_acl.ui[0].arn can be evaluated at count 0 and fail on
        # the index. one() on the splat yields null instead.
        self.assertIn(
            "web_acl_id = one(aws_wafv2_web_acl.ui[*].arn)",
            _read("cloudfront.tf"),
        )

    def test_every_waf_resource_carries_the_gate(self):
        # FOUR resources plus the rule-group data source, five blocks total:
        # ip_set, web_acl, log_group, web_acl_logging_configuration, and the
        # data source. This assertion was first written expecting six, on a
        # miscount stated twice before the test was run -- the count is the
        # reason the test exists, so it is pinned rather than derived.
        #
        # The data source matters as much as the resources: wafv2:GetRuleGroup
        # is denied to the deploy role, so an ungated lookup fails every PLAN
        # in every environment, not only the ones that would build an ACL.
        waf = _read("waf.tf")
        blocks = re.findall(
            r'^(resource|data)\s+"(aws_[a-z0-9_]+)"\s+"([a-z0-9_]+)"\s*\{',
            waf,
            re.M,
        )
        self.assertEqual(
            len(blocks), 5, f"expected 5 blocks in waf.tf, found {blocks}"
        )
        self.assertEqual(
            waf.count("local.ui_waf_enabled ? 1 : 0"),
            len(blocks),
            "every resource and data source in waf.tf must be gated on "
            "local.ui_waf_enabled; one without a count is created in every "
            "environment.",
        )


class TestTheFindingIsNotSuppressed(unittest.TestCase):
    def test_aws_0011_is_not_ignored_anywhere(self):
        # The direct replacement for the lost signal. trivy does not report
        # AVD-AWS-0011 today because it cannot resolve the one() expression,
        # NOT because it was excused -- and those two look identical in a
        # green gate. If the wiring is ever removed or changed so the finding
        # returns, it must return honestly rather than be silenced.
        #
        # Checked across the whole root, including bootstrap/, because a
        # directive anywhere in a scanned tree suppresses the check for the
        # resource it precedes.
        offenders = []
        pattern = os.path.join(INFRA_DIR, "**", "*.tf")
        for path in glob.glob(pattern, recursive=True):
            with open(path, encoding="utf-8") as f:
                if "trivy:ignore:AVD-AWS-0011" in f.read():
                    offenders.append(os.path.relpath(path, INFRA_DIR))
        self.assertEqual(
            offenders,
            [],
            f"AVD-AWS-0011 is suppressed in {offenders}. The WAF finding is "
            f"accepted by this test suite and the compliance deadline below, "
            f"not by a trivy directive -- a directive would hide the finding "
            f"returning if the wiring changed.",
        )


class TestTheComplianceDeadline(unittest.TestCase):
    def test_the_waf_is_live_before_the_compliance_deadline(self):
        # THIS TEST IS A DEADLINE, AND IT IS MEANT TO START FAILING.
        #
        # Everything above passes while the WAF is off, which is correct --
        # the flag being off is the reviewed position, not a defect. But that
        # means nothing in CI goes red for a WAF that never ships, and the
        # red trivy used to raise is gone. "Accepted until the grant lands"
        # is only honest if the acceptance expires.
        #
        # If this is failing: either the grant landed and the flag should be
        # on, or the program's date moved and COMPLIANCE_DEADLINE should move
        # with it -- with the new date's source recorded, the way waf.tf
        # records this one. Do not delete the test to make CI green.
        if datetime.date.today() <= COMPLIANCE_DEADLINE:
            return
        enabled = [
            env
            for env, text in _tfvars().items()
            if _assignment(text, "enable_ui_waf") == "true"
        ]
        self.assertTrue(
            enabled,
            f"Pearson's WAF & IP Sanctions program was due "
            f"{COMPLIANCE_DEADLINE.isoformat()} (owner PCM/SRE/Security) and "
            f"no environment has enable_ui_waf = true. The blocker is the "
            f"wafv2 grant on the deploy role: apply "
            f"infrastructure/deploy/uif-echo-dev-waf-grant.json, confirm "
            f"ui_allowed_cidrs, then set the flag in envs/dev-uif.tfvars.",
        )


if __name__ == "__main__":
    unittest.main()


def _acl_rules():
    """(name, priority, enforces, allows) for every rule in the web ACL.

    `enforces` covers both shapes that can stop a request: an explicit
    `action { block {} }`, and a referenced rule group left at
    `override_action { none {} }`, which lets the group's own block actions
    apply. A group set to `count` observes and never stops anything.
    """
    text = _read("waf.tf")
    acl = text[text.index('resource "aws_wafv2_web_acl" "ui"'):]
    rules = []
    for chunk in acl.split("\n  rule {")[1:]:
        name = re.search(r'name\s*=\s*"([^"]+)"', chunk)
        priority = re.search(r"priority\s*=\s*(\d+)", chunk)
        if not (name and priority):
            continue
        head = chunk.split("visibility_config", 1)[0]
        rules.append((
            name.group(1),
            int(priority.group(1)),
            bool(re.search(r"action\s*\{\s*block\s*\{\}", head)
                 or re.search(r"override_action\s*\{\s*none\s*\{\}", head)),
            bool(re.search(r"action\s*\{\s*allow\s*\{\}", head)),
        ))
    return rules


class TestEnforcementRunsBeforeAnythingIsAllowed(unittest.TestCase):
    """WAF short-circuits on the first rule with an action, so an allow rule
    placed before an enforcing one silences it for every request the allow
    matches. UIFPE-756 found exactly that: the source allow-list sat at
    priority 5, ahead of the rate limit and ahead of the one managed group the
    compliance program requires in block mode -- so for all traffic actually
    reaching the UI, that requirement did nothing. The allow-list is now
    0.0.0.0/0, which would have made it do nothing for EVERYONE.
    """

    def test_the_parse_found_both_kinds_of_rule(self):
        rules = _acl_rules()
        self.assertTrue(rules, "no rules parsed out of the web ACL")
        self.assertTrue(
            [r for r in rules if r[2]],
            "no enforcing rule parsed, so the order check below would pass "
            "for a trivial reason",
        )
        self.assertTrue([r for r in rules if r[3]],
                        "no allow rule parsed, same problem")

    def test_every_allow_rule_runs_after_every_enforcing_rule(self):
        rules = _acl_rules()
        last_enforcing = max(r[1] for r in rules if r[2])
        for name, priority, _, allows in rules:
            if not allows:
                continue
            with self.subTest(rule=name):
                self.assertGreater(
                    priority,
                    last_enforcing,
                    f"{name} allows at priority {priority}, ahead of an "
                    f"enforcing rule at {last_enforcing}. WAF stops at the "
                    f"first rule with an action, so every enforcing rule "
                    f"after this one is dead for any request it matches -- "
                    f"and with an open allow-list that is every request.",
                )

    def test_the_sanctions_group_still_runs_first(self):
        """It is the compliance program's own rule group, and nothing -- not
        even the allow-list -- may exempt itself from it."""
        rules = _acl_rules()
        sanctions = [r for r in rules if "sanctions" in r[0]]
        self.assertTrue(sanctions, "the pcm-sanctions rule is gone")
        self.assertEqual(
            min(r[1] for r in rules),
            sanctions[0][1],
            "pcm-sanctions is no longer the first rule evaluated",
        )
        self.assertTrue(sanctions[0][2], "pcm-sanctions is no longer enforcing")


# AWS's own constraint on a wafv2 description, copied from the API's error:
#   Member must satisfy regular expression pattern:
#   ^[\w+=:#@/\-,\.][\w+=:#@/\-,\.\s]+[\w+=:#@/\-,\.]$
# Word characters, + = : # @ / - , . and inner whitespace. NOT a semicolon,
# NOT parentheses, NOT an apostrophe. Terraform cannot know this, so a bad
# description plans clean and fails partway through an apply.
_WAFV2_DESCRIPTION = re.compile(
    r"^[\w+=:#@/\-,\.][\w+=:#@/\-,\.\s]+[\w+=:#@/\-,\.]$"
)

#: Every stack with a wafv2 web ACL. A cross-stack path has to be declared in
#: stacks/echo/ci-watch or this test never runs on the change that breaks it
#: (UIFPE-728).
_WAF_FILES = (
    "stacks/echo/infrastructure/terraform/waf.tf",
    "stacks/apicurio/infrastructure/terraform/waf.tf",
)


def _repo_root():
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".github").is_dir():
            return parent
    raise AssertionError("repo root not found")


class TestWafv2DescriptionsSatisfyAwsOwnRegex(unittest.TestCase):
    """A description AWS rejects fails PARTWAY THROUGH AN APPLY, not at plan.

    Hit twice. Apicurio first (UIFPE-36: parentheses and an apostrophe), then
    echo (UIFPE-756: a semicolon, in a line copied from Apicurio's and edited).
    Neither fix added a check, so the second was free to happen -- and it
    happened on the first apply this file ever had, after the ACL had sat
    written-but-never-applied behind a flag for weeks.

    This covers EVERY stack rather than the one that broke, which is the only
    version of this test worth having.
    """

    def _descriptions(self):
        root = _repo_root()
        found = []
        for rel in _WAF_FILES:
            path = root / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r'description\s*=\s*"([^"]*)"', text):
                found.append((rel, m.group(1)))
        return found

    def test_the_files_are_there_and_carry_descriptions(self):
        """A path that moved would make every assertion below vacuous."""
        found = self._descriptions()
        self.assertTrue(found, f"no descriptions parsed from {_WAF_FILES}")
        stacks = {rel.split("/")[1] for rel, _ in found}
        self.assertEqual(
            stacks,
            {"echo", "apicurio"},
            f"expected a waf.tf in both stacks, parsed {sorted(stacks)}",
        )

    def test_every_description_is_one_aws_accepts(self):
        for rel, description in self._descriptions():
            with self.subTest(file=rel, description=description[:40]):
                rejected = sorted(
                    {c for c in description
                     if not re.match(r"[\w+=:#@/\-,\.\s]", c)}
                )
                self.assertRegex(
                    description,
                    _WAFV2_DESCRIPTION,
                    f"{rel} has a description wafv2 will reject"
                    + (f" -- the characters it objects to are {rejected}"
                       if rejected else " -- check its first/last character,"
                       " which may not be whitespace")
                    + ". Terraform plans this clean and the apply fails at "
                      "Create*, after earlier resources are already made.",
                )


#: Every tfvars that feeds a wafv2 IP set, in any stack. A cross-stack path
#: must be declared in stacks/echo/ci-watch or this never runs on the change
#: that breaks it (UIFPE-728).
_IPSET_TFVARS = (
    ("stacks/echo/infrastructure/terraform/envs", "ui_allowed_cidrs"),
    ("stacks/apicurio/infrastructure/terraform/envs", "api_allowed_cidrs"),
)


class TestIpSetCidrsAreOnesAwsAccepts(unittest.TestCase):
    """wafv2 IP sets accept every IPv4 CIDR EXCEPT /0.

    Terraform plans `0.0.0.0/0` clean and the apply fails with
    WAFInvalidParameterException on field IP_ADDRESS -- after earlier
    resources in the same apply are already made. Found on UIFPE-756's second
    apply attempt, one failure after the description-regex one above, which is
    the same shape: a constraint only AWS enforces, on a value Terraform is
    happy with.

    "Allow everything" is therefore spelled as the two halves 0.0.0.0/1 and
    128.0.0.0/1, which together cover the whole space and are accepted.
    """

    def _entries(self):
        root = _repo_root()
        found = []
        for rel, var in _IPSET_TFVARS:
            directory = root / rel
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.tfvars")):
                text = path.read_text(encoding="utf-8")
                # The value may be inline or span lines, so take everything to
                # the closing bracket and pull the quoted CIDRs out of it.
                m = re.search(re.escape(var) + r"\s*=\s*\[(.*?)\]", text, re.S)
                if not m:
                    continue
                for cidr in re.findall(r'"([^"]+)"', m.group(1)):
                    found.append((f"{rel}/{path.name}", var, cidr))
        return found

    def test_the_tfvars_are_found_and_carry_cidrs(self):
        """A moved path or a renamed variable would make the check vacuous."""
        found = self._entries()
        self.assertTrue(found, f"no CIDRs parsed from {_IPSET_TFVARS}")
        stacks = {rel.split("/")[1] for rel, _, _ in found}
        self.assertEqual(
            stacks,
            {"echo", "apicurio"},
            f"expected CIDRs in both stacks, parsed {sorted(stacks)}",
        )

    def test_no_cidr_uses_the_one_prefix_aws_rejects(self):
        for where, var, cidr in self._entries():
            with self.subTest(file=where, cidr=cidr):
                self.assertFalse(
                    cidr.strip().endswith("/0"),
                    f"{where} has {var} containing {cidr}. A wafv2 IP set "
                    f"accepts every IPv4 range EXCEPT /0: Terraform plans it "
                    f"clean and the apply fails with "
                    f"WAFInvalidParameterException on field IP_ADDRESS. To "
                    f"allow everything, use 0.0.0.0/1 and 128.0.0.0/1.",
                )

    def test_every_cidr_is_shaped_like_a_cidr(self):
        """A bare address with no prefix is also refused by the API, and is an
        easy thing to paste in."""
        for where, var, cidr in self._entries():
            with self.subTest(file=where, cidr=cidr):
                self.assertRegex(
                    cidr.strip(),
                    r"^(\d{1,3}\.){3}\d{1,3}/([1-9]|[12]\d|3[0-2])$",
                    f"{where}'s {var} entry {cidr!r} is not an IPv4 CIDR with "
                    f"a prefix between /1 and /32.",
                )
