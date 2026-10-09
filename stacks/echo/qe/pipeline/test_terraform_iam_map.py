# test_terraform_iam_map.py
#
# Asserts infrastructure/deploy/terraform-iam-map.json still describes the
# Terraform root it is derived from.
#
# WHY. bootstrap-environment.sh renders an environment's deploy policy from
# that map, so a resource type present in the Terraform and absent from the map
# produces a policy missing its actions -- and the failure lands at the worst
# possible moment. It does not fail at plan: a plan reads, it does not create.
# It fails part-way through an APPLY, on a privileged one-time bootstrap run by
# a person, with some resources created and some not. apicurio needed FIVE
# apply-and-fix rounds to converge its own policy (UIFPE-496), and each round
# cost a half-finished environment.
#
# This cannot prevent a wrong ACTION LIST -- only AWS knows what CreateJob
# really needs, and the rounds are how that converges. What it prevents is the
# cheaper, likelier mistake: adding a resource type to the root and forgetting
# the map exists. That one is pure omission, and a test can see it.
#
# It also checks the map is internally coherent, because a typo in a scope
# token renders a policy with an empty ARN list rather than an error.
import fnmatch
import importlib.util
import json
import os
import re
import sys
import unittest


HERE = os.path.dirname(__file__)
# BOTH roots. This was the top-level root alone, on the premise that
# infrastructure/terraform-gold is "a separate release unit with its own deploy
# path ... the same boundary qe/ci.sh draws". UIFPE-576 reversed that premise,
# and here it had teeth: a deploy policy derived from one root grants nothing
# for the other root's resources, and a policy that grants nothing reads
# exactly like one that works until the apply fails on it.
TF_DIRS = (
    os.path.join(HERE, "..", "..", "infrastructure", "terraform"),
    os.path.join(HERE, "..", "..", "infrastructure", "terraform-gold"),
)
MAP_PATH = os.path.join(HERE, "..", "..", "infrastructure", "deploy", "terraform-iam-map.json")
GOLD_LOCALS = os.path.join(
    HERE, "..", "..", "infrastructure", "terraform-gold", "locals.tf"
)

# Scopes that emit no actions, so an entry using one needs no ARNs.
EMPTY_SCOPES = {"_skip", "_in_delta", "_local"}


def _extra_actions(entry):
    """Every cross-service action on an entry, whichever form it uses.

    `extra_actions` is a list that borrows the entry's `extra_scope`, OR a
    {scope: [actions]} map that names its own -- added for
    aws_lakeformation_resource, which needs two destinations. Iterating the map
    directly yields SCOPE NAMES, so a case that forgets this silently checks the
    wrong strings and passes.
    """
    x = entry.get("extra_actions") or []
    if isinstance(x, dict):
        return [a for actions in x.values() for a in actions]
    return list(x)


def _all_actions(entry):
    return (
        list(entry.get("actions", []))
        + list(entry.get("unscopable_actions", []))
        + _extra_actions(entry)
    )


def _load_map():
    with open(MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def _root_types():
    """(resource types, data types) declared across BOTH roots' *.tf files."""
    text = ""
    for tf_dir in TF_DIRS:
        for name in sorted(os.listdir(tf_dir)):
            if name.endswith(".tf"):
                with open(os.path.join(tf_dir, name), encoding="utf-8") as f:
                    text += f.read() + "\n"
    return (
        set(re.findall(r'^resource "([a-z0-9_]+)"', text, re.M)),
        set(re.findall(r'^data "([a-z0-9_]+)"', text, re.M)),
    )


class TestMapIsWellFormed(unittest.TestCase):
    def setUp(self):
        self.m = _load_map()

    def test_it_parses_and_has_the_four_sections(self):
        for key in ("_comment", "_scopes", "resources", "data"):
            self.assertIn(key, self.m)

    def test_every_scope_reference_resolves(self):
        # A typo here is silent: the renderer finds no template and emits a
        # statement with no ARNs, which reads as a deliberate "*" to no one.
        scopes = set(self.m["_scopes"]) - {"_comment"}
        for section in ("resources", "data"):
            for name, entry in self.m[section].items():
                for key in ("scope", "extra_scope"):
                    token = entry.get(key)
                    if token is None:
                        continue
                    with self.subTest(section=section, type=name, key=key):
                        self.assertIn(
                            token,
                            scopes,
                            f"{section}.{name}.{key} names '{token}', which is "
                            f"not in _scopes. The renderer would produce a "
                            f"statement with no resource ARNs.",
                        )

    def test_every_scope_is_used(self):
        # An unused scope is either a leftover from the apicurio port -- ECS,
        # ECR, ELBv2 and RDS all had one there and none applies here -- or a
        # resource entry that was meant to reference it and does not.
        scopes = set(self.m["_scopes"]) - {"_comment"} - EMPTY_SCOPES
        used = set()
        for section in ("resources", "data"):
            for entry in self.m[section].values():
                used.update(t for t in (entry.get("scope"), entry.get("extra_scope")) if t)
                # extra_actions may be a {scope: [actions]} map, which names its
                # own destinations instead of borrowing extra_scope. Missing
                # these would report a live scope as unreferenced.
                if isinstance(entry.get("extra_actions"), dict):
                    used.update(entry["extra_actions"])
        self.assertEqual(
            sorted(scopes - used),
            [],
            "these scopes are defined and referenced by nothing",
        )

    def test_a_non_empty_scope_carries_arns(self):
        for token, spec in self.m["_scopes"].items():
            if token == "_comment" or token in EMPTY_SCOPES:
                continue
            with self.subTest(scope=token):
                self.assertTrue(spec.get("arns"), f"{token} has no arns")
                # Every unscopable scope must justify itself. "*" without a
                # reason is how a policy quietly becomes account-wide.
                if spec["arns"] == ["*"]:
                    self.assertTrue(
                        spec.get("why"),
                        f"{token} grants \"*\" and gives no reason. An "
                        f"unscopable grant has to say why it cannot be scoped.",
                    )

    def test_entries_that_emit_nothing_say_so(self):
        # An entry with no actions and a real scope is indistinguishable from
        # an entry someone started and abandoned.
        for section in ("resources", "data"):
            for name, entry in self.m[section].items():
                if any(
                    entry.get(f)
                    for f in ("actions", "unscopable_actions", "extra_actions")
                ):
                    continue
                with self.subTest(section=section, type=name):
                    self.assertIn(
                        entry.get("scope"),
                        EMPTY_SCOPES,
                        f"{section}.{name} emits no actions but is not marked "
                        f"_skip, _in_delta or _local, so it reads as unfinished.",
                    )

    def test_an_entry_with_actions_is_not_on_a_scope_that_emits_nothing(self):
        # The converse of the case above, and the dangerous direction.
        # required-actions.py `continue`s on _skip, _in_delta and _local BEFORE
        # it looks at any action list, so an entry on one of those tokens emits
        # NOTHING -- including its unscopable_actions and extra_actions.
        #
        # Found by mutation while fixing aws_apigatewayv2_stage. Putting it back
        # on _in_delta with seven logs:*LogDelivery actions attached derived
        # zero of them, and nothing failed: the map reads as though the grant is
        # there, the policy renders without it, and the apply fails on an action
        # the map appears to list. That is the worst failure mode this file has,
        # because the evidence points away from the cause.
        #
        # aws_iam_openid_connect_provider was marked _skip for the same reason
        # once, and the header records it being "caught by required-actions.py
        # refusing the shape" -- but the refusal only covers `scope`, not an
        # entry that carries actions it will never emit.
        for section in ("resources", "data"):
            for name, entry in self.m[section].items():
                scope = entry.get("scope")
                if scope not in EMPTY_SCOPES:
                    continue
                carried = (
                    list(entry.get("actions", []))
                    + list(entry.get("unscopable_actions", []))
                    + _extra_actions(entry)
                )
                with self.subTest(section=section, type=name, scope=scope):
                    self.assertEqual(
                        [], carried,
                        "%s is on '%s', which the renderer skips before reading "
                        "any action list, so these are silently dropped: %s. "
                        "Give it a real scope -- the actions do not reach the "
                        "policy and the apply fails on one the map appears to "
                        "grant." % (name, scope, carried),
                    )

    def test_unscopable_actions_only_go_to_unscopable_scopes(self):
        # An action that takes no resource cannot be granted on specific ARNs:
        # the statement renders, matches nothing, and the apply fails on it.
        # This was real -- aws_wafv2_rule_group routed wafv2:ListRuleGroups to
        # the SCOPED wafv2 token until required-actions.py refused it.
        for section in ("resources", "data"):
            for name, entry in self.m[section].items():
                if not entry.get("unscopable_actions"):
                    continue
                extra = entry.get("extra_scope")
                with self.subTest(section=section, type=name):
                    self.assertIsNotNone(extra, f"{name} has unscopable_actions, no extra_scope")
                    self.assertEqual(
                        self.m["_scopes"][extra].get("arns"),
                        ["*"],
                        f"{name} routes unscopable_actions to '{extra}', which is "
                        f"scoped. Either the action is scopable and belongs in "
                        f"extra_actions, or extra_scope should name an "
                        f"unscopable token.",
                    )

    def test_cross_service_actions_are_not_on_the_resources_own_scope(self):
        # The subtlest error this map had, and the one with the worst symptom.
        # iam:PassRole belongs on the ROLE being passed, not on the Lambda
        # passing it; a layer published from S3 needs s3:GetObject on the
        # OBJECT, not the layer. Four entries carried these in `actions`, which
        # the renderer emits against the resource's OWN scope -- producing
        # iam:PassRole on arn:...:function:uif-echo-uat-*, which matches no role.
        # CreateFunction then fails with an error naming the role and nothing
        # naming the cause.
        CROSS = ("iam:PassRole", "s3:GetObject", "s3:GetObjectVersion")
        for section in ("resources", "data"):
            for name, entry in self.m[section].items():
                scope = entry.get("scope", "")
                # An entry whose own scope IS the iam or s3 scope is allowed to
                # list them in `actions` -- that is where they belong.
                if scope.startswith(("iam_", "s3_")):
                    continue
                for action in entry.get("actions", []):
                    if action in CROSS:
                        with self.subTest(section=section, type=name, action=action):
                            self.fail(
                                f"{name} lists {action} in `actions`, so it is "
                                f"granted on the '{scope}' scope's ARNs. Move it "
                                f"to `extra_actions` with an extra_scope naming "
                                f"the ARNs it actually applies to."
                            )


class TestMapCoversTheRoot(unittest.TestCase):
    """The assertion that actually earns this file's keep."""

    def setUp(self):
        self.m = _load_map()
        self.res, self.dat = _root_types()

    def test_the_root_declares_what_we_think_it_does(self):
        # Guards the parser. If the regex stopped matching, every assertion
        # below would pass against an empty set -- the vacuous-green failure
        # mode, one level up.
        # Both roots: 37 + 31 declared types with a large overlap, so 45 is a
        # floor a silently-empty second root would fall through.
        self.assertGreater(len(self.res), 45, f"parsed only {len(self.res)} resource types")
        self.assertGreater(len(self.dat), 7, f"parsed only {len(self.dat)} data types")

    def test_every_resource_type_in_the_root_is_mapped(self):
        missing = sorted(self.res - set(self.m["resources"]))
        self.assertEqual(
            missing,
            [],
            f"these resource types are in the Terraform and not in the IAM "
            f"map, so a rendered policy would omit their actions and the "
            f"bootstrap apply would fail part-way: {missing}",
        )

    def test_every_data_type_in_the_root_is_mapped(self):
        missing = sorted(self.dat - set(self.m["data"]))
        self.assertEqual(missing, [], f"unmapped data sources: {missing}")

    def test_the_map_describes_nothing_the_root_lacks(self):
        # The other direction, and it matters because this map was ported from
        # apicurio: an entry for a resource type Echo does not have is either a
        # copy that was not cleaned up, or a grant nothing justifies.
        #
        # aws_caller_identity is the one allowed exception. It is not declared
        # in the root, but the deploy scripts and the bootstrap call
        # GetCallerIdentity, and the map says so at the entry.
        extra = sorted(set(self.m["resources"]) - self.res)
        self.assertEqual(extra, [], f"mapped resource types absent from the root: {extra}")

        extra_data = sorted(set(self.m["data"]) - self.dat - {"aws_caller_identity"})
        self.assertEqual(extra_data, [], f"mapped data types absent from the root: {extra_data}")

    def test_the_derivation_script_sees_the_same_roots_this_test_does(self):
        # THE CONTROL THAT WAS MISSED. The roots are named twice -- TF_DIRS in
        # required-actions.py and TF_DIRS here -- so reverting the script to the
        # top-level root alone left this file green while the derived policy
        # silently lost every gold grant. Compare the script's own view instead
        # of trusting that the two lists agree.
        spec = importlib.util.spec_from_file_location(
            "required_actions",
            os.path.join(HERE, "..", "..", "infrastructure", "deploy",
                         "required-actions.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        script_res, script_dat = mod.tf_types(mod.TF_DIRS)
        self.assertEqual(
            script_res, self.res,
            "required-actions.py reads a different set of resource types than "
            "this test does, so the policy it derives does not match what the "
            "map is checked against",
        )
        self.assertEqual(script_dat, self.dat, "same, for data sources")


class TestTheDerivedPrefixMatchesTheGoldRoot(unittest.TestCase):
    """The policy must be derived for the -uif environment names, not the bare ones.

    stack_naming resolves the name prefix from the tfvars FILE, so the env
    string decides the prefix:

        --env dev       -> echo-dev        (the legacy stack)
        --env dev-uif   -> uif-echo-dev
        --env uat       -> echo-uat        (the legacy stack)
        --env uat-uif   -> uif-echo-uat

    The gold root has no override and no legacy mode: locals.tf computes
    `uif-echo-${var.environment}` unconditionally. So deriving a policy with
    the BARE env name scopes every ARN to echo-{env}-* and grants nothing at
    all for gold -- which reads exactly like a working policy until the
    converge fails on its first create.

    Pinned here rather than left to the bootstrap's caller, because the failure
    is silent and the fix is one word in a command line.
    """

    def test_the_gold_root_still_builds_its_prefix_from_the_uif_stem(self):
        with open(GOLD_LOCALS, encoding="utf-8") as f:
            locals_tf = f.read()
        self.assertIn(
            'name_prefix = "uif-echo-${var.environment}"', locals_tf,
            "the gold root's prefix formula changed. If it gained an override "
            "like the top-level root's name_prefix_override, this test and the "
            "env names the policy is derived for both have to change with it.",
        )

    def test_the_uif_env_names_resolve_to_the_gold_prefix(self):
        sys.path.insert(0, os.path.join(HERE, "..", "..", "src", "libs"))
        try:
            import stack_naming
        except ImportError as exc:  # pragma: no cover - environment problem
            self.skipTest(f"stack_naming not importable: {exc}")

        for env, expected in (("dev-uif", "uif-echo-dev"), ("uat-uif", "uif-echo-uat")):
            with self.subTest(env=env):
                names = stack_naming.load(env)
                self.assertEqual(
                    names.name_prefix, expected,
                    f"--env {env} resolves to {names.name_prefix}, so every ARN "
                    f"in the derived policy would be scoped to it",
                )
                self.assertEqual(
                    names.name_prefix, f"uif-echo-{names.environment}",
                    "the derived prefix no longer matches what the gold root "
                    "computes from the same environment value",
                )

    def test_the_bare_env_names_do_not(self):
        # The negative control. If these ever start resolving to uif-echo-*,
        # the trap above is gone and the warning should go with it -- but until
        # then, an operator who types `--env uat` gets a policy for the OTHER
        # stack, and this records that as known rather than surprising.
        sys.path.insert(0, os.path.join(HERE, "..", "..", "src", "libs"))
        try:
            import stack_naming
        except ImportError as exc:  # pragma: no cover
            self.skipTest(f"stack_naming not importable: {exc}")

        for env in ("dev", "uat"):
            with self.subTest(env=env):
                self.assertNotEqual(
                    stack_naming.load(env).name_prefix, f"uif-echo-{env}",
                    f"--env {env} now resolves to the uif prefix; the "
                    "docstring above and the bootstrap's guidance are stale",
                )

    def test_the_cross_boundary_bus_grant_matches_the_name_it_creates(self):
        # The one ARN in the policy outside the stack prefix. Pinned against the
        # formula in announce.tf, because the scope's correctness is not
        # checkable from the policy alone: event-bus/{prefix}-* would look
        # perfectly reasonable and authorise nothing.
        announce = os.path.join(
            HERE, "..", "..", "infrastructure", "terraform-gold", "announce.tf"
        )
        with open(announce, encoding="utf-8") as f:
            tf = f.read()
        self.assertIn(
            'name = "uif-event-${var.environment}-bus"', tf,
            "announce.tf no longer creates uif-event-{env}-bus. If the eventing "
            "layer took ownership of its own bus, the events_control_bus scope "
            "should go with it -- it is the only cross-prefix grant in the policy.",
        )
        arns = _load_map()["_scopes"]["events_control_bus"]["arns"]
        self.assertTrue(
            any("event-bus/uif-event-{env}-" in a for a in arns),
            f"the grant must be scoped to the EVENT stack's prefix, not echo's: {arns}",
        )

class TestActionsLandOnArnsThatCanMatchThem(unittest.TestCase):
    """An action granted on another service's ARN grants nothing at all.

    Both checks here exist because extending the map to the gold root needed
    FOUR scope changes, not just new entries -- and each one, left out, would
    have produced a policy that looks complete and authorises nothing:

      glue:CreateTrigger with no trigger/ ARN in the glue scope
      sqs:* with no sqs scope at all
      events:CreateEventBus against rule/ ARNs
      dynamodb:PutItem against the LEGACY table prefix, while gold's three
        tables are on the renamed one
    """

    # Resource type -> a substring at least one of its scope's ARNs must carry.
    # Only the ARN KINDS that are not implied by the service, which is what the
    # generic check below cannot see.
    REQUIRED_ARN_KINDS = {
        "aws_glue_trigger": "trigger/",
        "aws_glue_job": "job/",
        "aws_cloudwatch_event_bus": "event-bus/",
        "aws_cloudwatch_event_rule": "rule/",
        "aws_dynamodb_table_item": "table/{prefix}-",
        "aws_lambda_function": "function:",
    }

    def setUp(self):
        self.m = _load_map()

    def _arns(self, entry):
        out = []
        for key in ("scope", "extra_scope"):
            token = entry.get(key)
            if token and token not in EMPTY_SCOPES:
                out += self.m["_scopes"][token]["arns"]
        return out

    def test_every_action_has_an_arn_of_its_own_service(self):
        for section in ("resources", "data"):
            for name, entry in sorted(self.m[section].items()):
                arns = self._arns(entry)
                if arns == ["*"] or "*" in arns:
                    continue
                services = {a.split(":", 1)[0] for a in entry.get("actions", [])}
                services |= {a.split(":", 1)[0] for a in _extra_actions(entry)}
                for svc in sorted(services):
                    with self.subTest(type=name, service=svc):
                        self.assertTrue(
                            any(a.startswith(f"arn:aws:{svc}:") or f":{svc}:" in a
                                or (svc == "s3" and a.startswith("arn:aws:s3:"))
                                for a in arns),
                            f"{name} grants {svc}:* actions but none of its "
                            f"scope's ARNs is an {svc} ARN: {arns}",
                        )

    def test_the_arn_kinds_each_type_needs_are_present(self):
        for name, kind in sorted(self.REQUIRED_ARN_KINDS.items()):
            section = "resources" if name in self.m["resources"] else "data"
            if name not in self.m[section]:
                continue
            with self.subTest(type=name, kind=kind):
                arns = self._arns(self.m[section][name])
                self.assertTrue(
                    any(kind in a for a in arns),
                    f"{name} needs an ARN containing '{kind}' and its scope has "
                    f"none: {arns}. The actions would be granted on ARNs that "
                    f"cannot match, which authorises nothing.",
                )


class TestLogGroupNamesAreInsideTheLogsScope(unittest.TestCase):
    """Every log group the roots create must match an ARN in the `logs` scope.

    THE FAILURE. The uat apply (run 36253830424) died creating
    aws_cloudwatch_log_group.api_access_logs:

        not authorized to perform: logs:CreateLogGroup on resource:
        arn:aws:logs:eu-west-1:...:log-group:/aws/apigateway/uif-echo-uat

    The action was granted. The map lists logs:CreateLogGroup and always has.
    What was missing was an ARN the name could match: the scope held
    `*{prefix}-*`, `/aws/lambda/{prefix}-*` and `aws-waf-logs-{prefix}-*`, and
    the group is named EXACTLY `/aws/apigateway/{prefix}` -- nothing after the
    prefix, so the trailing `-*` cannot match, and it is not under /aws/lambda/.

    WHY THIS CLASS AND NOT ANOTHER ACTION CHECK. Every other IAM round in this
    file was a missing ACTION, and a missing action is at least visible in the
    map as an absence. This one was a name that fell a single hyphen outside a
    glob -- the action present, the statement rendered, the policy attached, and
    the grant still authorising nothing. Reading the map cannot find it; only
    comparing the names against the patterns can.

    HOW THE NAMES ARE MODELLED, because it is an approximation. `name` is an
    interpolated string, so `${local.name_prefix}` is substituted with the
    prefix and every other `${...}` with `{prefix}-x` -- a stand-in for the
    resource-derived names, which are all prefixed in these roots. That is
    faithful enough to have caught this bug and is not a Terraform evaluator; a
    log group named from something genuinely unprefixed would need reading here.

    Groups gated off for uif stacks are skipped. The three shared Glue groups
    (/aws-glue/jobs/output, /error, /logs-v2) carry
    `count = var.environment == "dev" && !local.is_uif_stack ? 1 : 0` because
    the name is fixed account-wide and all four Echo stacks would otherwise
    claim it -- so they are the legacy dev stack's, deliberately, and asserting
    the uif scope covers them would demand a grant nothing uses.
    """

    PREFIX = "uif-echo-uat"

    @classmethod
    def setUpClass(cls):
        cls.m = _load_map()

    def _scope_name_globs(self):
        """The log-group-name half of each ARN in the `logs` scope."""
        out = []
        for arn in self.m["_scopes"]["logs"]["arns"]:
            _, _, tail = arn.partition(":log-group:")
            if tail:
                out.append(tail.replace("{prefix}", self.PREFIX))
        return out

    def _declared_group_names(self):
        """(file, resource, modelled name) for groups a uif stack creates."""
        out = []
        for tf_dir in TF_DIRS:
            if not os.path.isdir(tf_dir):
                continue
            for fname in sorted(os.listdir(tf_dir)):
                if not fname.endswith(".tf"):
                    continue
                with open(os.path.join(tf_dir, fname), encoding="utf-8") as f:
                    src = f.read()
                for m in re.finditer(
                    r'resource "aws_cloudwatch_log_group" "([a-z0-9_]+)" \{(.*?)\n\}',
                    src, re.S,
                ):
                    body = m.group(2)
                    count = re.search(r"^\s*count\s*=\s*(.+)$", body, re.M)
                    if count and "!local.is_uif_stack" in count.group(1):
                        continue
                    name = re.search(r'^\s*name\s*=\s*"([^"]*)"', body, re.M)
                    if not name:
                        continue
                    modelled = name.group(1).replace(
                        "${local.name_prefix}", self.PREFIX
                    )
                    modelled = re.sub(r"\$\{[^}]+\}", self.PREFIX + "-x", modelled)
                    out.append((fname, m.group(1), modelled))
        return out

    def test_the_parser_finds_the_log_groups(self):
        # Guards both regexes. With either broken, the case below passes against
        # an empty set -- which is how a coverage test becomes decoration.
        found = self._declared_group_names()
        self.assertGreaterEqual(
            len(found), 3,
            "expected several log groups a uif stack creates; the parser may "
            "have stopped matching",
        )
        self.assertTrue(
            self._scope_name_globs(),
            "the `logs` scope yielded no log-group name patterns",
        )

    def test_the_apigateway_group_is_covered(self):
        # Named explicitly as well as covered by the sweep below, because this
        # is the one that actually broke an apply and a regression here should
        # say so by name rather than as one line of a list.
        self.assertTrue(
            any(
                fnmatch.fnmatchcase("/aws/apigateway/%s" % self.PREFIX, g)
                for g in self._scope_name_globs()
            ),
            "the API Gateway access-log group is named exactly "
            "/aws/apigateway/{prefix}. A pattern ending `-*` cannot match it.",
        )

    def test_every_declared_group_matches_some_arn(self):
        globs = self._scope_name_globs()
        unmatched = []
        for fname, res, name in self._declared_group_names():
            if not any(fnmatch.fnmatchcase(name, g) for g in globs):
                unmatched.append("%s (%s) -> %s" % (res, fname, name))
        self.assertEqual(
            [], unmatched,
            "these log groups are created by a uif stack and match no ARN in "
            "the `logs` scope, so logs:CreateLogGroup is granted on nothing "
            "that can match and the apply fails AccessDenied: %s. Patterns: %s"
            % ("; ".join(unmatched), globs),
        )


class TestTheSecondExtraScope(unittest.TestCase):
    """`extra_actions` as a {scope: [actions]} map, and the entry that needs it.

    aws_lakeformation_resource is the first entry needing TWO extra
    destinations. RegisterResource passes a role -- iam:PassRole on
    role/{prefix}-*, via iam_stack_roles -- and separately reads Lake
    Formation's service-linked role, which lives under role/aws-service-role/
    and is therefore outside that scope entirely.

    THE FAILURE. The uat apply returned a LakeFormation 400 wrapping an IAM 403:

        registering Lake Formation Resource (arn:aws:s3:::uif-echo-uat-silver):
        InvalidInputException: User: .../uif-echo-uat-deploy-role/GitHubActions
        is not authorized to perform: iam:GetRole on resource:
        role AWSServiceRoleForLakeFormationDataAccess

    The block sets no role_arn, so Lake Formation registers the location against
    its own service-linked role and validates that role AS THE CALLER. Nothing
    in the Terraform mentions it.

    WHY NOT JUST WIDEN iam_stack_roles. Because its `why` says it "cannot touch
    any other role in the account", and adding an aws-service-role ARN would
    make that sentence false while also granting PassRole on a service-linked
    role nothing passes. A one-line fix that falsifies the map's own
    documentation is worse than a schema change.

    Only needed on the FIRST registration -- later applies call DescribeResource,
    which reaches no IAM. That is why dev's hand-written policy never carried it
    and was no guide: dev's location was registered once, long ago, and its
    policy records what dev still needs rather than what it needed to get there.
    """

    SLR_ARN = (
        "arn:aws:iam::{account}:role/aws-service-role/"
        "lakeformation.amazonaws.com/AWSServiceRoleForLakeFormationDataAccess"
    )

    @classmethod
    def setUpClass(cls):
        cls.m = _load_map()
        cls.lf = cls.m["resources"]["aws_lakeformation_resource"]

    def test_the_entry_uses_the_map_form(self):
        self.assertIsInstance(
            self.lf.get("extra_actions"), dict,
            "aws_lakeformation_resource needs two extra scopes, which only the "
            "map form can express",
        )

    def test_both_destinations_are_present_and_distinct(self):
        # The point of the map form: two scopes, each with its own actions, and
        # nothing leaking between them. The SLR's exact action set is pinned by
        # test_the_slr_grant_is_an_allowlist_not_a_shape and deliberately not
        # repeated here -- it changed on three consecutive rounds, and two
        # copies of it meant two tests to update for one fact.
        extra = self.lf["extra_actions"]
        self.assertEqual({"iam:PassRole"}, set(extra.get("iam_stack_roles", [])))
        self.assertTrue(extra.get("iam_lakeformation_slr"))
        self.assertNotIn(
            "iam:PassRole", extra["iam_lakeformation_slr"],
            "nothing passes the service-linked role; PassRole here would be a "
            "grant on AWS's own role with no caller",
        )

    def test_the_slr_scope_is_one_exact_arn(self):
        # Not a wildcard. A pattern like role/aws-service-role/* would grant
        # GetRole on every service-linked role in the account, and there is
        # exactly one this needs.
        scope = self.m["_scopes"]["iam_lakeformation_slr"]
        self.assertEqual([self.SLR_ARN], scope["arns"])
        self.assertNotIn("*", scope["arns"][0])

    def test_the_slr_grant_is_an_allowlist_not_a_shape(self):
        # THIS CASE USED TO ASSERT READ-ONLY, AND THE ASSERTION WAS WRONG.
        # Registering a data location is how Lake Formation records which S3
        # prefixes the role may reach, and it records that in the role's INLINE
        # POLICY -- so iam:PutRolePolicy is genuinely required. Three rounds of
        # the uat converge found the three actions one at a time: GetRole,
        # then GetRolePolicy, then PutRolePolicy.
        #
        # A shape rule ("must start with Get") was the wrong tool: it encoded a
        # guess about Lake Formation's behaviour as a safety property, and when
        # the guess broke, the test looked like the authority and the policy
        # looked like the mistake.
        #
        # What actually keeps this safe is the ARN -- one exact service-linked
        # role -- so the verbs are an explicit allowlist, and the case below
        # pins the ones that must never appear.
        self.assertEqual(
            {"iam:GetRole", "iam:GetRolePolicy", "iam:PutRolePolicy"},
            set(self.lf["extra_actions"].get("iam_lakeformation_slr", [])),
            "adding an action here is a deliberate widening on a role AWS owns "
            "-- say in the scope's `why` which call needs it",
        )

    def test_the_slr_grant_cannot_take_the_role_over(self):
        # The real safety property, and the one a shape rule was standing in
        # for. Writing an inline policy to a service-linked role is what
        # registration does; replacing the role, its trust policy or its
        # attachments is not, and none of it is recoverable by re-applying.
        acts = set(self.lf["extra_actions"].get("iam_lakeformation_slr", []))
        forbidden = {
            "iam:CreateRole", "iam:DeleteRole", "iam:DeleteRolePolicy",
            "iam:AttachRolePolicy", "iam:DetachRolePolicy",
            "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:PassRole",
            "iam:CreateServiceLinkedRole", "iam:DeleteServiceLinkedRole",
        }
        self.assertEqual(
            set(), acts & forbidden,
            "these would let the stack replace or detach a role AWS owns: %s"
            % sorted(acts & forbidden),
        )

    def test_the_renderer_routes_each_action_to_its_own_scope(self):
        # The assertion that actually proves the schema change works. The three
        # cases above read the map; this one reads what the map RENDERS, which
        # is what AWS sees. A renderer that silently dropped the map form would
        # pass all three and grant nothing.
        spec = importlib.util.spec_from_file_location(
            "required_actions",
            os.path.join(HERE, "..", "..", "infrastructure", "deploy",
                         "required-actions.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["required_actions"] = mod
        spec.loader.exec_module(mod)
        _, by_scope, _ = mod.collect("uat-uif", "283556840805", "eu-west-1")
        self.assertIn("iam:GetRole", by_scope.get("iam_lakeformation_slr", set()))
        self.assertIn("iam:PassRole", by_scope.get("iam_stack_roles", set()))
        self.assertNotIn(
            "iam:GetRole", by_scope.get("lakeformation_unscopable", set()),
            "the SLR read must not land on the unscopable Lake Formation scope",
        )


class TestLookupByUrlNeedsTheList(unittest.TestCase):
    """A data source looked up by `url` must be granted a list action.

    THE FAILURE. The uat converge of 2026-09-26 (run 36251046059) planned 210
    resources and then died on `data.aws_iam_openid_connect_provider.github`:

        AccessDenied: ... not authorized to perform:
        iam:ListOpenIDConnectProviders on resource:
        arn:aws:iam::283556840805:oidc-provider/*

    The map granted `iam:GetOpenIDConnectProvider`, scoped to the one provider
    the data source names. That looks complete and is half the call. `iam.tf`
    looks the provider up by `url`, and AWS has no get-by-url API for an
    identity provider -- so the provider LISTS every provider in the account to
    resolve the url to an arn, and only then gets it.

    WHY A DERIVATION CANNOT SEE THIS. The map is built from the resource and
    data types the Terraform declares. It sees the one provider being read; it
    cannot see that *reaching* it requires reading all of them. The map's own
    header says as much -- "blind to the post-create reads, tag reads and
    list-shaped calls the AWS provider makes around them ... a green
    `--verify-only` is therefore not evidence the apply will work" -- and this
    is that, arriving on schedule.

    The test is narrow on purpose. `url` is the only lookup attribute in either
    root today that has no get-by-X equivalent, so generalising to "every
    non-arn lookup attribute needs a list" would assert a rule this repository
    has one instance of and would misfire on the next data source looked up by
    a real identifier. What it pins is the instance that actually cost a run.
    """

    @classmethod
    def setUpClass(cls):
        cls.m = _load_map()
        text = ""
        for tf_dir in TF_DIRS:
            for name in sorted(os.listdir(tf_dir)):
                if name.endswith(".tf"):
                    with open(os.path.join(tf_dir, name), encoding="utf-8") as f:
                        text += f.read() + "\n"
        cls.tf = text

    def _data_types_looked_up_by_url(self):
        found = set()
        for m in re.finditer(r'^data "([a-z0-9_]+)" "[^"]+" \{(.*?)^\}', self.tf,
                             re.M | re.S):
            if re.search(r'^\s*url\s*=', m.group(2), re.M):
                found.add(m.group(1))
        return found

    def test_the_url_lookup_is_still_there(self):
        # Guards the regex above. If the block is reshaped and this finds
        # nothing, the case below would pass against an empty set.
        self.assertIn(
            "aws_iam_openid_connect_provider", self._data_types_looked_up_by_url(),
            "expected iam.tf to look the GitHub OIDC provider up by url; if "
            "that changed to a lookup by arn, this class is obsolete",
        )

    def test_every_url_looked_up_data_type_can_list(self):
        for name in sorted(self._data_types_looked_up_by_url()):
            entry = self.m["data"].get(name)
            with self.subTest(type=name):
                self.assertIsNotNone(entry, f"data {name} is not in the map")
                actions = set(entry.get("actions", []))
                actions |= set(entry.get("unscopable_actions", []))
                actions |= set(_extra_actions(entry))
                self.assertTrue(
                    any(a.split(":", 1)[1].startswith("List") for a in actions),
                    f"data {name} is looked up by url and there is no "
                    f"get-by-url API, so resolving it calls a List. Granted "
                    f"only {sorted(actions)}, the plan fails with AccessDenied "
                    f"on the list -- after printing a complete plan, which is "
                    f"what makes it easy to misread as a plan that worked.",
                )

    def test_the_list_is_granted_unscoped(self):
        entry = self.m["data"]["aws_iam_openid_connect_provider"]
        self.assertIn(
            "iam:ListOpenIDConnectProviders", entry.get("unscopable_actions", []),
            "a list-all action takes no resource condition, so it cannot sit "
            "in `actions` on iam_oidc_read -- that scope names one provider "
            "ARN and the list would be granted on an ARN it can never match",
        )

    def test_the_unscopable_iam_token_carries_no_write(self):
        # iam_unscopable is granted on "*". A write routed here would be
        # account-wide on IAM, which is the one place in this map where a
        # careless entry is a privilege escalation rather than a broken plan.
        scope = "iam_unscopable"
        self.assertEqual(["*"], self.m["_scopes"][scope]["arns"])
        landed = set()
        for section in ("resources", "data"):
            for entry in self.m[section].values():
                if entry.get("extra_scope") == scope:
                    landed |= set(entry.get("unscopable_actions", []))
                    landed |= set(_extra_actions(entry))
                if entry.get("scope") == scope:
                    landed |= set(entry.get("actions", []))
        self.assertTrue(landed, f"nothing routes to {scope}; it should be removed")
        bad = sorted(
            a for a in landed
            if not a.split(":", 1)[1].startswith(("List", "Get", "Describe"))
        )
        self.assertEqual(
            [], bad,
            f"{scope} is granted on * -- only read-shaped IAM actions belong "
            f"here. These are not reads: {bad}",
        )



class TestAttributesThatImplyExtraActions(unittest.TestCase):
    """A Terraform ATTRIBUTE can require an IAM action the resource type does not.

    Setting `reserved_concurrent_executions` on a Lambda makes the provider call
    PutFunctionConcurrency, which is a separate authorization from
    CreateFunction. The gold apply created 98 resources and then failed four
    times over on it (run 36334893083) -- one error per function that sets it.

    THE GENERAL SHAPE, and why it keeps costing rounds. The map is keyed by
    resource TYPE, so it can express "a Lambda needs CreateFunction". It cannot
    see that a particular argument on a particular Lambda adds a call. Every
    round of this converge that was not a scope mistake has been this: an
    attribute, a lookup by a non-key field, an access-log setting. The map's
    header calls it blindness to "post-create reads, tag reads and list-shaped
    calls"; attribute-implied actions belong on that list too.

    So this pins the pairs we have learned, derived from the Terraform rather
    than listed here -- a NEW Lambda that sets concurrency needs no edit, and
    removing the action from the map fails.
    """

    # Terraform attribute -> the action its presence requires.
    IMPLIED = {
        "reserved_concurrent_executions": "lambda:PutFunctionConcurrency",
        "access_log_settings": "logs:CreateLogDelivery",
    }

    @classmethod
    def setUpClass(cls):
        cls.m = _load_map()
        text = ""
        for tf_dir in TF_DIRS:
            if not os.path.isdir(tf_dir):
                continue
            for name in sorted(os.listdir(tf_dir)):
                if name.endswith(".tf"):
                    with open(os.path.join(tf_dir, name), encoding="utf-8") as f:
                        text += f.read() + "\n"
        cls.tf = text
        cls.granted = set()
        for section in ("resources", "data"):
            for entry in cls.m[section].values():
                cls.granted |= set(_all_actions(entry))

    def test_the_attributes_are_actually_used_somewhere(self):
        # Guards the table. An attribute no root sets would make its row vacuous
        # and the whole class would pass while granting nothing.
        for attr in sorted(self.IMPLIED):
            with self.subTest(attribute=attr):
                self.assertRegex(
                    self.tf, r"(?m)^\s*%s\b" % re.escape(attr),
                    "no root sets %s any more; drop the row or find out why"
                    % attr,
                )

    def test_every_implied_action_is_granted(self):
        missing = []
        for attr, action in sorted(self.IMPLIED.items()):
            if not re.search(r"(?m)^\s*%s\b" % re.escape(attr), self.tf):
                continue
            if action not in self.granted:
                missing.append("%s requires %s" % (attr, action))
        self.assertEqual(
            [], missing,
            "the Terraform sets an attribute whose provider call is not "
            "granted, so the apply fails per resource that sets it: %s"
            % missing,
        )


if __name__ == "__main__":
    unittest.main()
