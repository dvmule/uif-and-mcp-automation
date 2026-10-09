# JSM Alignment — OPERATE

Jira Service Management (JSM) is where UIF capabilities are operated once they are live. This document defines how operational work is tracked, so that every domain shipped through the FDE workflow has a clear operational home and ownership. A QE Lead and FDE use it to register and run support for what they ship.

## What JSM tracks

| JSM type | Use for |
|---|---|
| Incident | An unplanned interruption or degradation of a live capability. |
| Major Incident | A high-severity incident requiring coordinated response. |
| Problem | The underlying cause behind one or more incidents. |
| Change | A controlled change to a live capability. |
| Service Request | A standard, pre-approved request from a consumer. |

## Handoff from delivery to operations

The final step of the FDE workflow (`../architecture-design/operating-model/fde-build-deploy-workflow.md`, step 17) is registering operational ownership in JSM. A domain is not "done" until it has an operational owner and is reachable through JSM.

## The loop back to delivery

- A **Problem** whose fix requires code becomes a Jira **Bug** or **Story** in Jira Software (`jira-workflow.md`).
- Operational signals come from the **Observability** capability (`../capabilities/observability/`).

**JSM = OPERATE.**
