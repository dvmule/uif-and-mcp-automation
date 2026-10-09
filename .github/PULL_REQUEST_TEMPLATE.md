<!-- Title: UIFPE-<n> · <what this delivers>       e.g. UIFPE-39 · Product Data API
     The key links the PR to the issue. The rest has to name the thing you changed.

     Everything in these HTML comments is guidance. It is stripped before the
     Pull Request check measures your text, so you can leave it or delete it. -->

## Summary

<!-- What changed and why. The reasoning a reviewer needs before they open the
     diff, and the only description the PM and QE Lead will read.
     Say what problem it solves, not which files you touched — the diff already
     lists the files. -->

## Verification

<!-- How you know it works. Be concrete: the command you ran and its result, the
     CI job that proves it, the environment you deployed to. An unticked
     checklist is not evidence. -->

## Capability and domain

<!-- Which capability standard this work follows (API, Event, Schema, MCP, …) and
     which domain it ships in. Delete if it touches neither. -->

## Definition of Done

<!-- Tick these yourself before requesting review. They are the exit criteria from
     DEFINITION-OF-DONE.md §3 — the four that a reviewer cannot check for you.
     Leave this section visible: a reviewer needs to see what you claim. -->

- [ ] Merging to `develop` **and** `validate` — never `main`
- [ ] Every acceptance criterion on the Jira issue is evidenced above, individually.
      "Tested locally" is not evidence
- [ ] Contracts changed under `src/schemas/` are **registered**, not just edited
- [ ] Anything I found and did **not** fix is raised as its own issue and linked here

<!-- New to UIF? Read DEFINITION-OF-DONE.md before your first PR. It says what each
     board status means, who moves it, and when work is finished. Note that
     "Definition of Done" means three different things in this repo — §0 of that file
     disambiguates them, and it is the most common way to get confused here.

     Also true, and enforced by CI rather than by you:
       - every commit carries UIFPE-<n> and a descriptive #comment
       - your commit author email is your own Jira account email, no +alias
     The Smart Commit, Commit Identity and Pull Request checks enforce those two.
     See capabilities/delivery/jira-github-integration.md -->
