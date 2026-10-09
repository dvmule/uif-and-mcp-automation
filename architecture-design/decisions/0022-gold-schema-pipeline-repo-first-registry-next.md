# 0022. The Gold Schema Pipeline: One Flow, Repo First, Registry Next

- Status: Proposed
- Date: 2026-09-23
- Builds on [ADR-0018](0018-gold-runtime-is-a-stack-concern.md), whose decision 3 supplies the
  ownership test used throughout: *would a second domain need exactly this, unchanged?*

In plain terms: each business entity has one file describing it — every attribute, and which
audiences are allowed to see each one. A person edits that description through a web form, and the
platform turns it into a separate, narrower schema for each way the data leaves: an API, an event
feed, a partner export. Those schemas are what consumers build against, so they have to be
published somewhere consumers can read them.

Today they are published as files in this repository. The intent is that a **schema registry**
becomes the place consumers read them from, and the files become a stepping stone rather than the
destination. This record names the five steps of that flow, says who owns each one, and says what
changes when the registry arrives — so that the work done in the meantime lands on the registry
rather than having to be rebuilt.

## Context

### The one file everything derives from

Each entity has a **gold master schema**: `domains/{domain}/src/schemas/gold/{entity}/v{n}/master.json`.
It is the entity's full engineering description, and an `x-channels` tag names the audiences allowed
to receive each attribute. **Every top-level attribute must declare one** — there is no default,
because an omitted tag means nobody decided, and a guess would publish an attribute to consumers
because someone left out a line. An attribute *nested* inside another may omit the tag, and then
inherits its parent's audiences; it has a parent to inherit from, which is precisely what a
top-level attribute lacks.

Three audiences exist today:

| Audience | Delivered by | Who reads it |
|---|---|---|
| `event` | a queue | services reacting to changes |
| `message` | a stream | the export platform, and partner feeds built on it |
| `api` | a request/response surface | callers asking for one entity |

The audiences are concentric — anything on `event` is also on `message` and `api` — and each one is
produced by filtering the master down to the attributes tagged for it. That filtered copy is a
**tier projection**, and it is what a consumer should ever see. An attribute tagged with an empty
list reaches no audience at all: that is how a source-system key is kept inside the platform.

### The master already declares what a pipeline needs

This matters more than it looks, because it removes a whole category of configuration. A master
already states, in itself:

| Declaration | What it answers |
|---|---|
| `x-channels` per attribute | which audiences may receive it |
| `registry.{group,artifact,compatibility}` | the coordinate it is registered under |
| `metadata.owner.service` | the owning service, which registration requires |
| `metadata.lifecycle` | whether it is a draft or ready to publish |
| `metadata.{domain,entity,version}` | its identity |

A pipeline that reads these needs no configuration file of its own. One already does: the existing
per-domain publish workflow reads the owning service straight out of the master rather than being
told it.

### What exists today, and what does not

Five steps. Three are built, two are partial:

1. **Authoring.** A person edits an entity through the gold authoring wizard, which writes a branch
   and opens a pull request — authored by that person, not by the tool. **Built.**
2. **Compilation.** A workflow on the authoring branch runs the delta compiler over the submitted
   intake and pushes the generated master, mapping and query back onto the same branch. **Built.**
3. **Projection.** One generator derives all three tier projections from the master. **Built, but
   nothing triggers it** — a person runs it, and a test notices afterwards if they did not, for one
   entity out of five.
4. **Placement.** Each projection is committed where its audience's consumers look, by the rule
   below rather than by a convention inside whatever happens to call the generator (UIFPE-554):

   | Tier | Directory | Filename |
   |---|---|---|
   | `api` | `domains/{domain}/src/schemas/channels/{entity}/` | `{entity}-data-api-{operation}.json` |
   | `event` | `domains/{domain}/src/schemas/channels/{entity}/` | `{entity}-data-event-{operation}.json` |
   | `message` | `domains/{domain}/src/schemas/channels/{entity}/` | `{entity}-data-message-{operation}.json` |

   **Every tier lands in the same place**, because a projection is a schema whichever channel
   carries it, and the schema capability has a tree of its own. An earlier draft of this rule kept
   the api tier beside the contract that references it — that made one channel of three a special
   case, and left a directory named for channels holding only two of them.

   `{entity}` is hyphenated everywhere it appears in a path or a filename, while the master and the
   gold table keep the underscore form. That is one transform, and it is the same one the registry
   coordinate already applies.

   Every tier is scoped by entity, so a domain that hosts two entities keeps their artifacts in
   separate directories instead of relying on a shared filename prefix.

   A **contract is not a projection** and stays in its own capability folder,
   `domains/{domain}/src/api/{entity}/openapi.yaml`, scoped by entity for the same reason. It
   references its projection across the two trees — which is what the eventing contract in the same
   repository already does, so the pattern is not new.

   **One implementation**, `committed_path()` and `contract_path()` in the generator, and a test
   asserts this table and those functions agree in both directions — the same arrangement the
   resource-naming standard has with its hook. **Built.**
5. **Registration.** Projections are published to the registry, with a compatibility check first.
   **Built for one domain, and the endpoints it posts to are served by no code here** — the client
   sits in the medallion stack, the registry's own service is a different stack, and the two agree on
   the payload but not on the path. The compatibility check has nothing to talk to.

### Two traps this flow has already fallen into

**Restating instead of deriving.** At one point four things independently answered "which attributes
does this audience get": two generators, the same filtering logic hand-copied into each domain's
guard test, and a consuming platform holding its own list of column names. Every artifact that was
*derived* from a master was found correct; every place that *restated* a master had drifted.

**Filtering the body is not enough if the envelope is inherited.** Separately from the above, a
generator correctly removed the source-system keys from a projection's attributes and then copied
the master's descriptive block verbatim — which named those same keys, and their source-system
column names, in a schema built for consumers. A projection is only as narrow as its narrowest part.

**A glob that matches nothing exits successfully.** An earlier attempt to serve a second domain by
copying the first domain's workflow produced a green run that had validated nothing, because the
copy's file patterns still pointed at the original domain. Any generic step in this flow has to fail
when it finds nothing to do.

## Decision

### 1. The flow is five steps, and each has exactly one implementation

```
   authoring UI                                    ① a person edits an entity
        │   writes a branch + pull request
        ▼
   intake envelope  ──► delta compiler ──────────► ② master.json, mapping, query
        │                                             pushed to the same branch
        ▼
   master.json  ──► tier projection generator ───► ③ one projection per audience
        │                                             regenerated whenever the master moves
        ▼
   {domain}/src/…  ─────────────────────────────► ④ each projection beside its consumers
        │
        ▼
   schema registry  ◄───────────────────────────── ⑤ registered, compatibility checked first
```

No step may have a second implementation. A component that needs a projection **derives it from the
master through the one generator, or reads it from where that generator wrote it** — it never
reimplements the filtering and never restates a master's contents.

### 2. The master is the configuration. There is no separate config file.

A generic pipeline discovers its work by finding the masters
(`domains/*/src/schemas/gold/*/v*/master.json`) and reading each one's own declarations. Adding an
entity means adding a master; adding a domain means nothing at all.

A configuration file listing entities, audiences, coordinates or owners would restate what the
masters already say, and would become the next thing to drift. The only settings that legitimately
live outside a master are per-environment endpoints, which are already environment variables.

### 3. `metadata.lifecycle` is the publish gate

Whether an entity is registered is a property **of that entity**, declared in its master, not a
repository-wide switch. A master marked as a draft is validated and projected but not registered;
moving it off draft is what publishes it.

This replaces per-domain enable flags. It is finer-grained — one domain may have a live entity and a
draft one — and it puts the decision in the file the reviewer is already reading.

### 4. Ownership follows ADR-0018's question, which splits this flow in three

| Step | Owner | Why |
|---|---|---|
| ① authoring UI, ② compilation, ③ projection | the medallion stack, `stacks/echo` | every domain needs these unchanged; they encode no domain's data |
| ④ the masters themselves, and where their projections land | `domains/{domain}/` | a master encodes one domain's data and its consumers |
| ⑤ registration, compatibility, version negotiation | the registry stack, `stacks/apicurio` | every domain needs it unchanged, and it is the registry's own subject matter |

Step ⑤ is the one that moves. Registration currently lives beside the projection generator in
`stacks/echo/src/apps/cli/`, which makes the medallion stack responsible for talking to a registry
it does not own. The registry stack already owns both halves of what registration needs — the
*standard* for coordinates, which masters cite for the coordinate they declare, and the *service*
that registers them. Only the client sits elsewhere, which is why the two have drifted apart on the
endpoint path while still agreeing on the payload.

Projection does **not** move. It is a filtered view of the gold master, and ADR-0018 assigned the
gold control plane to `stacks/echo`. Moving projection to `stacks/apicurio` would make registration
a prerequisite for generating a schema, which inverts the dependency: a domain could not see its own
event payload shape without a registry.

### 5. Per-domain triggers, one shared body

A single workflow cannot serve every domain, because its file patterns must be scoped to one
domain's paths — that is what produced the false-green copy. But a per-domain workflow leaves the
body with no shared home, and the body then duplicates *within* one file rather than across them.

The shape is therefore a **reusable workflow** invoked by thin per-domain callers: each caller
declares only its own paths and passes its domain name; the body exists once. The repository's own
mental model already describes its CI and CD this way, though no workflow implements it yet — the
first one to do so sets the precedent.

Every generic step **fails closed**: a pattern that matches no master, no manifest and no projection
is an error, not a pass.

### 6. Repo first, registry next — and the registry is the destination

While the registry is unavailable, a projection committed in the repository is what consumers read,
and an API contract points at it by path. This is **transitional**, and every generated projection
says so in itself, so that a reader of the file cannot mistake it for the source of truth.

When the registry is available, three read paths change, and nothing else does:

| Consumer | Today | Then |
|---|---|---|
| API contract | points at a committed file | points at a registry coordinate |
| Event contracts | committed files | resolved from the registry |
| Export platform | reads a master from object storage | resolves the `message` projection from the registry |

The flow's first five steps are unchanged by that switch. This is the point of naming them now:
work built in the meantime must be built so that the switch is a change of read path, not a rebuild.

**Any interim mechanism that reads a schema from the repository or from object storage is
transitional and must be labelled as such where it is written.** A component that copies a schema's
contents into its own configuration is not transitional — it is a restatement, and decision 1
forbids it.

## What this does not change

- **How an entity is authored.** The wizard, the intake envelope and the delta compiler are
  unchanged.
- **What a projection contains.** The audience tags in the master decide that, and they already do.
- **Who reviews a schema change.** It arrives as a pull request authored by a person, and that stays
  true whether the destination is a file or a registry.
- **The concentric audience rule.** `event ⊆ message ⊆ api` is asserted by the generator and remains
  asserted.

## Alternatives considered

**A per-domain configuration file describing entities, audiences and coordinates.** Rejected: it
restates what each master already declares, and a restatement is the failure mode this flow has
already suffered. The masters are the configuration.

**One workflow for all domains.** Rejected on evidence: it has been attempted, and a file pattern
still scoped to the original domain produced a run that validated nothing and reported success.

**Move projection into the registry stack alongside registration.** Rejected: a projection is a view
of the gold master, which ADR-0018 assigned to the medallion stack, and the dependency would invert
— generating a schema would require the registry.

**Wait for the registry before building any of this.** Rejected: the registry's arrival changes three
read paths and nothing else. Naming the flow now is what makes the interim work land on the
registry instead of having to be redone.

## Consequences

**Good.** One implementation per step, so a change to the audience rules happens in one place.
Adding an entity is adding a master; adding a domain costs a thin caller. The publish decision is
visible in the file under review. The registry cutover is a change of read path in three consumers
rather than a re-plumbing.

**Costs.** Registration moves stacks. The publish path has never executed, so nothing has to be
migrated in flight — but it also means the move cannot be validated against a working baseline, and
reconciling the endpoint contract is part of the work rather than a follow-on. The
reusable-workflow refactor touches every domain's CI entry point. Making the generator run
automatically on a master change means the authoring branch gains a generated commit, which
reviewers must learn to expect.

**Risks.** The `lifecycle` gate is only as good as the review that lets a master move off draft —
it is a declaration, not a check, and it should not be mistaken for an approval workflow. Today it
is weaker still: the publishing caller passes a fixed lifecycle and never reads the master's, so a
draft entity would be registered as active. Decision 3 is therefore a change, not a description.
And a generic pipeline that fails closed will block a domain whose master is malformed; that is the
intended behaviour, but it means a broken master stops that domain's publishing rather than
degrading quietly.

**What carries this.** Three steps have gaps, and one ticket each: regenerating the projections when
a master moves (UIFPE-550, step ③ to ④), the reusable workflow with per-domain callers
(UIFPE-551, decision 5), and moving registration to the registry stack (UIFPE-552, decision 4).
Reading the `message` projection from the registry rather than object storage is the second phase of
UIFPE-540. Nothing here is built by this record.

**Not addressed here.** Whether the registry is reachable from every runtime that would need it,
what a consumer does when the registry is unavailable, and how schema versions are negotiated
across a breaking change. Each needs its own decision, and none of them blocks the five steps above.
