# Reference Architectures

## What this is

End-to-end blueprints showing how the horizontal capabilities compose into a working vertical domain. Where `patterns/` documents one recurring shape, a reference architecture wires many together into a complete, deployable picture — the answer to "what does a good domain look like from request to production?"

## What a reference architecture contains

- The domain slice it models and the capabilities it draws on (API, Event, Schema, Semantics, MCP/A2A/Agent, Infrastructure, Security, Observability, Delivery).
- The runtime topology: what deploys where on AWS/Kubernetes, wired with Terraform and Helm.
- The CI/CD path: GitHub Actions gates and GitHub Actions promotion Development → UAT → Production.
- Its security posture and observability signals, stated as design inputs.

## Relationship to reference-implementations

A reference architecture is the diagram-and-prose blueprint. The runnable, production-grade example that realizes it lives in `../../delivery-lab/reference-implementations/`.
