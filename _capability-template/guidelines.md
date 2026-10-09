# <Capability Name> — Guidelines (the worked standard)

> **Template — the mould every capability's `guidelines.md` is stamped from.** Copy this into `capabilities/<capability>/guidelines.md` and fill in each section, keeping the same five-section shape every other capability uses (`observability/guidelines.md`, `security/guidelines.md`, etc. are worked examples). Delete this note and the bracketed placeholders once filled in.

This is the detailed, do-this companion to the <Capability Name> capability `README.md`. It tells a Forward Deployed Engineer exactly how to name [the capability's key artifacts], document [its contract], test [that the contract holds] end to end, and what a build/CI gate enforces — so the standard is a checked property of every module, not a claim in a README.

This document follows the same **template shape** as every capability's `guidelines.md`: five sections — Naming, Contract documentation, Testing → automation → build, Standards & guidelines, Definition of done.

**Link, don't duplicate.** If another capability's `guidelines.md` already owns a mechanism this capability depends on (e.g. identity/secrets in `../infrastructure/kubernetes.md`, CI/CD shape in `../delivery/guidelines.md`), reference it — don't restate it here. State plainly what this document owns that the others don't.

Grounding standards (public, verifiable — not fabricated Pearson-internal rules): _list the real, citable standards this capability's guidance is grounded in (e.g. an RFC, a W3C/OASIS spec, an OWASP standard, a CNCF/OCI spec, SemVer). If a fact is Pearson-specific and not a public standard, it must carry its own explicit citation per the repo's source-of-truth convention: "Source: Confluence `<space>` page `<title>`, verified `<date>`" — never assert a Pearson-specific fact without one._

Worked example: _link to the concrete instance elsewhere in the repo that satisfies every rule below (a real file in `domains/<domain>/`, or a reference implementation under `delivery-lab/reference-implementations/`) — every capability's guidelines.md points at one._

---

## 1. Naming

_How an FDE names this capability's key artifacts — be concrete and give a real example, not just a pattern. Cross-reference another capability's naming scheme where this one must match it (e.g. Security's classification tiers matching Data's)._

---

## 2. Contract documentation (state the contract, not just the implementation)

_What this capability's contract is, where it's declared, and a minimal worked example (the smallest version that satisfies every rule) — not a full production file. Link to the full version elsewhere in the repo. State plainly what CI can mechanically check about this contract vs. what only human review can catch — don't overclaim automation._

---

## 3. Testing → automation → build

_The ordered pipeline of checks specific to this capability — numbered list, each step naming a concrete tool or technique (not "run tests" but what kind, against what). Then the CI wiring: where in the pipeline (`../delivery/guidelines.md` §3) this capability's checks plug in, and what triggers them (which `paths` filter)._

---

## 4. Standards & guidelines (non-negotiable design inputs)

_Bullet list of design-time rules — the things a design must state up front, not retrofit later. Cross-reference the capabilities this one is a design input to, or depends on as a design input (Security and Observability are explicitly design inputs to everything, per `../../CLAUDE.md`)._

---

## 5. Definition of done (<Capability Name>)

- [ ] Naming follows §1.
- [ ] Contract documented per §2, with a real worked instance somewhere in the repo.
- [ ] Testing/automation/build checks in §3 pass and are wired into CI, not a manual step.
- [ ] Design-time rules in §4 are visibly satisfied, not assumed.
- [ ] Maps to a Jira Story (`capability:<capability>` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

Worked instance: _link(s)_.
