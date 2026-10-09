# harbor / src

The shape convention this stack will follow once real code lands — mirrors
`stacks/beacon/src/` (`apps/` + `libs/`), the one proven pattern in this
repo for a deployable stack.

```
src/
├── apps/
│   ├── pipeline/          the import job — pulls from SFTP/API, transforms via ingest template
│   ├── control_panel/     always-on API — job catalog, run history, registries
│   └── lambdas/           record_run, emit_event, and similar light executors
├── libs/                  the ingest-template engine — shared, imported by apps/, never deployed alone
├── api/openapi.yaml       registered API contract
├── events/                event contracts this stack publishes/consumes
├── schemas/               data contracts and physical schemas
├── mcp/                   MCP manifest (config, not a server)
├── a2a/                   A2A manifest (config)
└── Dockerfile             (or a declared multi-image split, per CHECKLIST.md)
```

Nothing above exists yet. It gets built when a real Story names the first
partner feed and target domain — not before, per this repo's rule against
scaffolding capability folders a stack doesn't use yet.
