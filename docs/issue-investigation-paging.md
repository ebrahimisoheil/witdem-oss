# Issue investigation paging contract (development)

`witdem.analytics.issue_pages` defines version 1 of a proposed bounded detail
contract for failure investigations and members of a canonical retry group.
It is not an HTTP route, a released API promise, or a scalable query engine.
The existing Issues response remains unchanged.

## Semantics

Requests select `failures` or `retry_runs`, a scope identifier, a page size from
1 through 100, and an optional structured continuation cursor. Retry-run requests
require the canonical retry key, never the display label. Failure requests must
not include a retry key. Failures use the existing breakpoint predicate, including
recovered runs; membership is not the terminal-failure summary count.

Responses carry the complete selected investigation's `total`, the requested page
size, ordered items, and an explicit `next_cursor` or null. Execution identities
are unique and ascending. Continuations select identities strictly greater than
the last returned identity. PostgreSQL adapters must use equivalent binary
ordering rather than locale-dependent collation. The total never means the number
of returned preview items. A final page has no continuation.

## Scope and authorization

The serving adapter must derive the 64-character lowercase SHA-256 scope identifier
from a versioned canonical representation of the authorized tenant, effective
filters, and retained immutable snapshot or read-model revision. It must not trust
a scope identifier supplied by the client as authorization. Requests/cursors are
selectors only; access checks remain mandatory for every page.

The cursor repeats the scope, investigation kind, retry key, schema version and
last execution identity. Changing any selection or snapshot requires a first-page
request with a fresh scope. An adapter without retained snapshots must reject a
stale revision explicitly and ask for a restart; it must not silently continue over
a changed population. SQL values must remain bound parameters.

## Adoption gates

`page_issue_investigation` is an in-memory reference for storage-adapter parity
tests over complete authorized projections. It does not verify snapshot retention,
authorization, or source coverage itself and does not promise bounded database work.

Before enabling paging in a dashboard, implement and qualify the storage query,
transport validation and revision handling in both serving paths. Introduce explicit
overview totals and detail continuation links together; existing UI code derives
some counts from embedded arrays and must not reinterpret previews as full totals.
Keep existing clients compatible or use an explicit API version transition. Verify
multi-page completeness, filtered membership, tenant separation, stale revisions,
empty results and concurrent publication. Release/pin the upstream package before
downstream runtime adoption. No frontend redesign is required by this contract.
