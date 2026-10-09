# JPD Alignment — WHY

Jira Product Discovery (JPD) is where UIF work begins: it captures the WHY before any Capability, Epic, or code exists.

**A TPM has joined: Ausra Klimaviciene** (as of 2026-10-02) — a Technical Program Manager, not a product-discovery PM who'd take over this stage. WHY-ownership stays unchanged with the **Engineering Owner** (Satyanarayana Gunavanthan) and **Product Owner** (Rajnivas Karunakaran). Ausra's role is cross-initiative scope planning — she plans BAA & UIF scope directly with both owners — program coordination, not discovery judgment. **This line's trigger ("once a PM/SM is onboarded") hasn't actually fired yet** — re-check if a discovery-owning PM or a Scrum Master is separately onboarded.

## The discovery flow

```
Idea → Opportunity → Initiative
```

- **Idea** — a raw problem, request, or hypothesis.
- **Opportunity** — a validated problem worth solving, with evidence.
- **Initiative** — a committed direction that delivery organises around.

## The UIF Initiative

**What this Initiative is:** Unified Integration Fabric — the Engineering Operating System for enterprise APIs, events, data, semantics, knowledge graphs, MCP, A2A, and agents.

**Potential Benefits:** replaces fragmented ESB/ETL/SSDP point-to-point integrations with governed, reusable APIs, event streams and MCP tools — 1,220 → 435 integrations, Release 3.

**Linked Domain Epic(s):** one per domain — e.g. `UIFPE-51` (Domain: Product), `UIFPE-88` (RELAY Echo).

**Capability label(s) exercised:** all thirteen, as each domain needs them — `capability:api`, `capability:event`, `capability:data`, `capability:mcp`, etc.

A Capability label has no completion percentage — it's horizontal, cutting across every domain that uses it. The Domain Epic is what's actually measured. Every Story traces to a Domain Epic; capability stays a label on it (`jira-workflow.md`).

## Related Initiative: Business Assurance Agent (BAA)

Two sources disagree on what BAA is, and this document isn't resolving that silently:

- The **UIF Project Plan** (Confluence page 1479999498) treats it as an external downstream consumer: *"Business Assurance Agent — depends on MCP/AI agent-ready APIs — mid-Aug 2026, scope TBC."*
- **UIF's own operating structure** lists BAA as one of UIF's three outcome squads — tagged a strategic initiative, though the squad is defined rather than staffed (no named members, no row in "Who holds what") — with the primary outcome *"Enterprise change assurance, delivered through BAA."*

Both can be true — a squad defined to deliver, serving a consumer whose discovery sits outside UIF — but it's worth reconciling explicitly, not assuming. No Jira *project* named BAA exists, and no Confluence page other than the UIF Project Plan describes it.

**New as of 2026-09-24 — a third source, and it leans away from the consumer reading.** Two Jira records outside UIFPE now describe BAA; neither existed when the two bullets above were written:

- **TBPJPD-336** — a real JPD Initiative in the `TBPJPD` *Tech Business Partners* product-discovery project, created 2026-09-23, status `Next`, reported and assigned to **Rajnivas Karunakaran** — UIF's own Product Owner. Titled "GIP/BAA — Business Assurance for *Agentification*" (a different expansion from "Business Assurance *Agent*"). Milestones 30 Sep 2026 kickoff → 22 Dec 2026 MVP sign-off; discovery partner Cognizant; ARB governance; hard constraint "no agent in the runtime data path".
- **OCTOPOPE-562** — "UIF & BAA", Epic, `Done` (2026-09-09). The funding business case treats **UIF and BAA as one investment**, delivering the canonical entity contracts *and* "a production Business Assurance Agent".

This does not resolve the conflict — both sources above still say what they said — but it retires the "discovery hasn't produced anything concrete" reading. **The reconciliation is now a live decision:** if BAA is funded with UIF and its Initiative is owned by UIF's Product Owner, the Project Plan's downstream-consumer row is the entry that is wrong.

**Linked Domain Epic(s):** none yet — blocked on UIF's own MCP/Agent capability delivery.

**Update, 2026-10-02 — a TPM (Ausra Klimaviciene) has joined**, planning BAA & UIF scope directly with the Product Owner and Engineering Owner. Staffing fact, not a resolution, and not the PM/SM this trigger meant. The BAA conflict (external-consumer reading vs. own-outcome-squad reading) is still unresolved; re-check once that's actually settled, or once a discovery-owning PM/SM joins.

## Handoff to delivery

When an Initiative is committed, the Engineering/Product Owners and FDE create the Jira Capability and its first Epic in Jira Software. JPD holds the WHY; Jira Software holds the WHAT and delivery (`jira-workflow.md`); JSM holds OPERATE (`jsm-alignment.md`).

**JPD = WHY.**
