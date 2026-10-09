# Capabilities × Domains — one model in GitHub and Jira

UIF runs on two crossing axes, and the same crossing must show up identically in the repo and in Jira. This page is the worked example that keeps them in sync: it takes one real slice of work and shows exactly where it lives in GitHub and exactly how it is traced in Jira. If the two ever drift, this page is the reference to reconcile against.

## The two axes

- **Capabilities are horizontal** — reusable disciplines (API, Event, Schema, …). They define *how* a thing is built well, once, for the whole fabric. In GitHub they are `capabilities/<capability>/` (standards, no code). In Jira they are **Capability** issues (hierarchy level 2) with a **Foundation Epic** for the platform-wide groundwork of that discipline.
- **Domains are vertical** — deployable, ownable business units (product, customer, hr, …). They are *where* code ships and *who* owns it. In GitHub they are `domains/<domain>/` (all code, one container image). In Jira they are **Domain** epics (hierarchy level 1) that hold the delivery work.

A unit of delivery sits at the **intersection**: it ships in a domain and conforms to one or more capability standards.

## The rule that keeps GitHub and Jira aligned

| Axis | GitHub | Jira | Role |
|---|---|---|---|
| Capability (horizontal) | `capabilities/<c>/` standard + `domains/<d>/src/<c>/` impl | **Capability** + **Foundation Epic**; `capability:<c>` label | the discipline the work conforms to |
| Domain (vertical) | `domains/<d>/` | **Domain epic**; `domain:<d>` label | the deployable unit that owns the work |
| Delivery work | a PR touching `domains/<d>/src/**` | **Story** under the **Domain epic**, labelled with its `capability:*` | the intersection |

**Parent = domain; label(s) = capability.** Work *ships* in a domain (the domain owns the deploy, the PR, the DoD gate), so a delivery Story's parent is its Domain epic. The capability it exercises is carried as a `capability:*` label, so a Story can conform to more than one discipline. This gives both roll-ups from one backlog:

- *"How much API work is in flight?"* → `labels = capability:api`
- *"What is the product domain delivering?"* → `labels = domain:product`

Platform-wide, non-domain groundwork (stand up the schema registry, publish the CI template) stays under a **Capability Foundation Epic** and carries no `domain:*` label.

## Worked example — product developer-registration API

One slice, shown in both systems.

### In GitHub

```
capabilities/api/         # the API standard (how any API is built)      ← horizontal
stacks/apicurio/standards/      # the Schema standard (register-first)         ← horizontal
domains/product/
├── src/
│   ├── schemas/          # developer-registration contract, registered FIRST
│   ├── api/              # openapi.yaml + handlers for the endpoint
│   ├── apps/             # business logic
│   └── Dockerfile        # builds the one product image
└── infrastructure/       # how that image runs (terraform, k8s, helm, obs)
```

The PR touches `domains/product/**`; the root `.github/workflows/domain-ci.yml` runs the product job and gates on schema + Dockerfile + qe.

### In Jira

```
Domain: product                         (Epic, label domain:product)          ← vertical delivery
└── Build product developer-registration API   (Story)
        parent : Domain: product
        labels : capability:api, domain:product
        conforms to: API Foundation (Capability: API) + Schema Foundation (Capability: Schema)
```

The Story lives under the **product Domain epic** (it ships there) and is labelled `capability:api` (the discipline it conforms to). The **API** and **Schema** Capabilities own the standards it must satisfy; the link to them is the label, not the parent.

## Keeping them the same

1. Every domain in `domains/` has a matching **Domain epic** in Jira (`domain:<name>` label).
2. Every capability in `capabilities/` has a matching **Capability** + **Foundation Epic** in Jira (`capability:<name>` label).
3. Every delivery Story is parented to its **Domain epic** and carries the `capability:*` label(s) for the disciplines it touches.
4. **Architecture & Design is not a capability** — it is the frame the 13 sit in (`../../architecture-design/`). In Jira it is a program/foundation concern (`program:architecture-design`), not one of the 13 `capability:*` tags.

The definitive capability list is `../../jira/capability-model.md`; the Jira workflow and traceability model is `../../jira/jira-workflow.md`.
