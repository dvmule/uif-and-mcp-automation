# transform-ui — the gold authoring wizard

The wizard a business owner uses to add or change a gold attribute. Every
submission becomes a pull request rather than live state, so the
delta compiler, the parity gates and human review apply to it like any other
change.

Served at `/m/transform/` on the shared RELAY CloudFront distribution, from a
prefix of the same bucket that hosts the Echo shell at its root. It is a sibling
app rather than a route inside `../ui` until UIFPE-97 folds it into the shell:
the two carry different dependency sets, so that is a port, not a move.

It looks like part of Echo meanwhile: `src/styles/tokens.css` carries Echo's
RELAY tokens under the same names (Echo's `ui/src/lib/__tests__/tokenParity.test.ts`
fails if they drift), and the left rail's other entries are full-page links to
Echo's routes, opening collapsed when Echo's rail was left collapsed.

| Task | Command |
|---|---|
| Dev server | `npm --prefix stacks/echo/transform-ui run dev` |
| Build | `npm --prefix stacks/echo/transform-ui run build` |
| Tests | `npm --prefix stacks/echo/transform-ui run test` |

The four services it calls — compile preview, grain probe, AI draft and submit —
are Lambda function URLs owned by `../infrastructure/terraform-gold`. Their URLs
are not committed: `deploy-echo-gold-dev.yml` reads them from that root's
Terraform outputs and passes them to the build as `VITE_*` variables, so a local
build leaves them empty and each feature degrades to its unconfigured state.
