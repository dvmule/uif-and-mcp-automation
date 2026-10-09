# Four-in-a-Box

UIF delivery is run by a four-person operating model. Each role owns one clear question, so that at any point in discovery, delivery, or operations it is unambiguous who decides. This document defines the four roles and what each owns; a PM uses it to staff a squad, an FDE uses it to know where their authority begins and ends. Roles map onto the capability model in `../jira/capability-model.md`.

| Role | Owns the question | In one word |
|---|---|---|
| Product Manager | Why are we building this? | WHY |
| Forward Deployed Engineer | How is it built, shipped, and run? | HOW |
| UX Lead | What is the experience? | EXPERIENCE |
| QE Lead | Is it good enough to trust? | QUALITY |

## Product Manager — WHY

Owns the problem and the outcome, not the solution.

- Product vision, business outcomes.
- Roadmap, prioritisation.
- **JPD Initiatives** — discovery from Idea → Opportunity → Initiative (see `../jira/jpd-alignment.md`).

## Forward Deployed Engineer (FDE) — HOW

Owns end-to-end technical execution, from architecture through code to production support. The FDE is the center of gravity of UIF delivery.

- **Architecture:** solution architecture, API design, event design, domain modelling.
- **Delivery:** story decomposition, sprint execution, technical leadership.
- **Engineering:** coding, CI/CD, platform engineering, DevSecOps.
- **Cloud platform:** AWS, Kubernetes, Terraform.
- **Data & AI:** canonical data models, semantic data models, data contracts, schema registry, semantics, knowledge graph, MCP, A2A, agent workflows.
- **Operations:** monitoring, incident resolution, production support.

The FDE workflow is defined in `fde-build-deploy-workflow.md`.

> Note the Data & AI split from the capability model: the FDE builds **canonical/semantic data models and data contracts** (the Data capability — information modelling) and the **schema registry** (the Schema capability — representation). UIF does not own data products, data governance, quality, lineage, or stewardship (`../jira/capability-model.md`).

## UX Lead — EXPERIENCE

Owns how the capability is experienced by its users, including developer-facing and agent-facing surfaces.

- Experience design, user journeys.
- Accessibility.
- Design standards and design system.

## QE Lead — QUALITY

Owns the definition of trustworthy and the means to verify it. Quality is designed in with the FDE, not inspected at the end.

- Test strategy, automation, performance.
- Reliability and quality governance.
- Works against the Observability capability's signals and the domain-level Definition of Done in `../../PLANNING.md`.

## How the four interact across the lifecycle

| Lifecycle stage | PM | FDE | UX | QE |
|---|---|---|---|---|
| Discovery (JPD) | Leads | Advises feasibility | Shapes experience | Advises testability |
| Design | Sets outcome | Leads architecture | Leads journeys | Defines quality bar |
| Build (`domains/`) | Prioritises | Leads | Reviews UX | Builds tests |
| Ship (CI/CD) | — | Leads | — | Gates on quality |
| Operate (JSM) | Owns value | Owns support | — | Owns reliability |
