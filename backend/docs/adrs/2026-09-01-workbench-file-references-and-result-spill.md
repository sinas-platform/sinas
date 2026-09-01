# ADR: Workbench file references in tool calls + result spill

- **Status:** Accepted
- **Date:** 2026-09-01
- **Authors:** Kjeld Oostra (with Claude)
- **Related code:**
  - `backend/app/services/workbench_refs.py` — the reference resolver and result spill
  - `backend/app/services/tool_execution.py` — resolution before dispatch; spill at the truncation point
  - `backend/app/services/workbench.py` — the workbench this builds on (see the workbench ADR)
  - `backend/app/services/tool_result_store.py`, `truncate_tool_result` — the prior art this completes

## Context

With the workbench in place, one gap remained in how file content moves:
it had to travel **through the model**. Sending a workbench PDF to a
connector meant the model copying base64 into a tool-call argument —
token-expensive, size-capped, binary-hostile. In the other direction, an
oversized tool result was structure-aware-truncated for the context, and
because truncation ran before any persistence, the excess was **lost for
good** — even `retrieve_tool_result` served the truncated copy.

We also considered a "workbench remotes" interface (checkout/promote
targets beyond collections: git, Confluence, Drive). See "What we'd NOT
do" for why that lost the sequencing argument.

## Decision

### Outbound: file references

Any tool-call parameter value that is exactly the typed object

```json
{"$workbench": "report.pdf"}          // optionally: "encoding": "text" | "base64"
```

is resolved by the tool executor at dispatch time: the file's current
version is loaded from the **calling chat's** workbench and the object is
replaced by the content (UTF-8 text for textual content types by default,
base64 otherwise or on request). The model passes a reference; the tool
receives bytes. This works for **every** tool kind — connectors,
functions, pipelines — with zero per-service code.

Design choices with reasons:

- **Typed object, not a magic string.** `workbench://…` inside a string
  can collide with legitimate content and invites accidental resolution;
  a dict whose keys are exactly `{$workbench, encoding?}` cannot appear by
  accident in model-emitted JSON arguments.
- **Automatic on the sentinel, not schema-annotated opt-in.** Connector
  schemas come from OpenAPI and would each need annotation plumbing; the
  collision-proof sentinel makes opt-in unnecessary. A cheap string
  pre-check keeps the no-reference case free.
- **Failure fails the call.** A bad path, missing file, non-UTF-8 text
  request, or a file over `workbench_ref_max_bytes` returns an error
  result for that tool call — the sentinel must never leak through to a
  tool as literal arguments.
- **Chat-scoped by construction.** The resolver only ever reads the
  calling chat's own workbench (ownership double-checked), so a reference
  cannot reach any other user's or chat's files.

### Inbound: result spill

At the existing truncation point, when the chat's agent has the workbench
enabled, the **full** result is first written to the workbench
(`tool_results/<tool>_<call-id>.<json|txt>`, private, with
`file_metadata: {origin: "tool", tool_name, tool_call_id}` as provenance),
and the truncated inline copy carries a pointer (`_full_result` key inside
JSON results, a text suffix otherwise). The model can then page through
the full result with `workbench_read` offsets or process it wholesale with
code execution — which the sandbox sync makes natural, since the spilled
file materializes into the working directory.

Without a workbench, behavior is exactly as before (truncate-only). The
spill is best-effort: any failure degrades to truncate-only, never to a
failed tool call.

## Impact

| Component | Change |
|---|---|
| `workbench_refs.py` (new) | resolver, spill, pointer attachment |
| `tool_execution.py` | resolve before dispatch; spill at truncation |
| `workbench.py` | `workbench_list` description advertises the convention |
| `config.py` | `workbench_ref_max_bytes` (default 10 MB) |

## Open questions

1. Should `retrieve_tool_result` / the tool-result store learn about
   spilled files (serve the workbench copy when the store holds a
   truncated one)? Left alone for now — the pointer in the inline result
   is the discovery mechanism.
2. Reference support in *message content* (a user attaching
   `{"$workbench": …}` in structured content parts) — plausible later,
   out of scope here.

## What we'd NOT do

- **Workbench remotes first.** A checkout/promote provider interface for
  external systems (git, Confluence, Drive) was considered and
  deliberately deferred: its value over file references is round-trip
  provenance with conflict detection, and that contract is only honest
  where the service has real version primitives (git SHAs, Confluence
  page versions). Drive-class services lack atomic compare-and-swap —
  the interface would be thin for two services and a pile of per-connector
  exceptions for the rest. File references + spill deliver both directions
  ("upload this workbench file via any connector operation", "save this
  download into the workbench") with zero per-service code. If remotes
  return, they come capability-tiered (`conflictDetection: version | etag
  | none`, last-write-wins-with-warning on `none`) and probably ship for
  git first — or only.
- No schema annotations, no per-connector mapping config, no URL-mode
  resolution (signed URLs for parameters can layer on later if a
  connector needs a URL rather than bytes).

## Next steps

1. Ship (this PR).
2. When a real "update the source document in place" need appears, revisit
   remotes at the reduced scope above — the provenance stamps on spilled
   and checked-out files already carry the bookkeeping a provider would
   need.
