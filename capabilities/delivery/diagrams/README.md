# Diagram sources

D2 sources for the three pipeline diagrams published on Confluence page
[6.2 CI/CD — Bootstrap, Infra & Promotion](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1530560709).
They live here rather than loose in a working directory so the diagrams stay
reviewable in version control, and so a correction to the pipeline can be made
to the source that produced the picture.

| Source | Shows |
| --- | --- |
| `flow-apicurio-buildtag-01.d2` | feature branches to develop merges to the CI gate to the build tag |
| `flow-apicurio-uat-02.d2` | build tag into uat: infra converges, the app promotes, the release tag |
| `flow-apicurio-prod-03.d2` | the same tag into prod, illustrated; dashed means it does not exist yet |
| `seq-apicurio-cdc-poller-01.d2` | the Delivery Lab CDC sequence, on page [1477247076](https://pearson.atlassian.net/wiki/spaces/UIF/pages/1477247076) — replaced an ASCII swim lane |

Every identifier in them is real, taken from the chain that produced
`apicurio/v0.2.3` and `release/uat/2026-09-21.1` on 2026-09-21.

## Rendering and republishing

```bash
d2 flow-apicurio-buildtag-01.d2 flow-apicurio-buildtag-01.svg
rsvg-convert -w 1200 flow-apicurio-buildtag-01.svg -o preview.png   # then look at it
```

Two checks before publishing, both of which catch a silent failure:

```bash
grep -c foreignObject flow-apicurio-buildtag-01.svg   # must be 0
grep -o '<text' flow-apicurio-buildtag-01.svg | wc -l # roughly one per label
```

A `foreignObject` means a label was written as a D2 markdown block. Those need an
HTML rendering engine, so they come out as empty coloured boxes when the SVG is
shown as an image, which is exactly how Confluence shows an attachment. `d2`
reports success either way, so the count is the only warning you get. Use plain
quoted labels with `\n` for line breaks.

Upload with the `gip-confluence` skill's `push_to_confluence.py --fix-images-batch`,
which needs a Confluence API token; the MCP has no attachment scope.

## A sequence diagram in D2, against the usual advice

`seq-apicurio-cdc-poller-01.d2` is a sequence diagram, and the `gip-confluence`
skill routes sequences to Mermaid on the grounds that D2 has no sequence syntax
worth using. It was written in D2 anyway, for a reason worth recording:

- **Mermaid renders through Kroki here, and Kroki was returning HTTP 500.** There
  is no local Mermaid CLI on this machine, so every Mermaid render goes to
  Kroki. Five attempts with 45 seconds of backoff all failed. D2 runs locally
  and has no such dependency.
- **The skill's judgement is still right in general.** A first attempt in D2
  using standalone boxes for the two notes rendered them as extra participants
  with their own lifelines, which was worse than the ASCII it replaced.
- **What made it work was grouping.** D2 nests messages under a labelled block,
  so the poller half sits in a red `unreachable today` group and the operator
  half in a green `works today` group. For this page that reads better than a
  flat Mermaid sequence, because the page's subject is precisely that one half
  does not run.

If Kroki comes back, a Mermaid version would be a reasonable swap for the other
sequences in this space. Do not take this file as a reason to stop using Mermaid
for sequences generally.

**One landmine confirmed while doing it:** Kroki rejects `rect rgb(...)` in
Mermaid source — the parentheses read as the documented alias-parenthesis
failure. `<br/>` inside a `Note over` is fine. Both were tested; the 500s that
followed were Kroki being intermittently unavailable rather than a syntax fault,
which is easy to misread as a syntax fault.
