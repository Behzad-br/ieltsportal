# LMS API contract (stable conventions)

Internal reference for Modules 21+. OpenAPI (`/api/openapi.json` in non-production) lists routes; this document covers **shared semantics**, not every endpoint.

## Base URL

- Browser calls `{apiOrigin}/api/...`
- Local Vite: empty origin → same-origin `/api` proxy
- Production SPA on `fti4iltes.tech`: defaults to `https://api.fti4iltes.tech` unless `VITE_API_URL` is set
- **Never** put `DATABASE_URL` or backend secrets in frontend / `VITE_*`

## Authentication

- Header: `Authorization: Bearer <JWT>`
- Student/teacher identity for skill operations is **JWT-derived** (`auth_users.id`)
- `X-Student-Id` / `X-Teacher-Id` are **legacy**; ignored for JWT users when `AUTH_LEGACY_HEADERS=false` (production)
- Status policy:
  - **401** — missing/invalid token
  - **403** — authenticated but not allowed
  - **404** — missing **or** intentionally hidden private resource (prefer 404 over leaking existence)

## Errors

Application errors:

```json
{ "detail": "Human-readable message" }
```

Validation (422) may use FastAPI’s array-of-errors form. Frontend must use `formatApiDetail` / `messageFromErrorBody` — never `String(object)`.

Conflicts (uniqueness / lifecycle): **409**.

## Timestamps

- Stored as naive UTC in PostgreSQL
- Serialized as ISO-8601 strings (often with `Z` when helpers append it)
- Treat API datetimes as UTC; do not interpret as local wall-clock without conversion

## Null / pending scores

- Missing bands are **`null`**, never `0`
- Result helpers may include:
  - `result_pending` — teacher/AI final not yet published to student
  - `band_source` — `none` | `estimated` | `teacher` | similar module-specific labels
- `estimated_band` ≠ teacher final; use `teacher_band` / `final_band` / `overall_band` per module

## Band fields

| Field | Meaning |
|-------|---------|
| `raw_score` | Objective skill raw marks (R/L) |
| `estimated_band` | Auto/AI estimate |
| `teacher_band` | Teacher override (W/S/HW) |
| `final_band` | Authoritative published band when set |
| `overall_band` | Mock aggregate of **available** skill bands (single-skill mocks are not four-skill IELTS) |

## Pagination

Most catalogs are small and unbounded by design. Where limits exist they are server-side (e.g. analytics/history caps). Prefer `items` + optional `total` when adding new paginated lists; do not introduce a second pagination dialect without need.

## Speaking path legacy

Speaking student routes remain under `/api/tests`, `/api/answers`, `/api/history`, `/api/progress` (not `/api/speaking/...`). Admin speaking uses `/api/speaking/admin/...`.

## Idempotent actions

Repeated submit/complete/finalize returns **stable authoritative JSON** (same keys; bands only when published where applicable). Client must not invent status transitions.

## Versioning

Routes are unversioned `/api/...`. Do not add `/api/v1` unless a breaking cutover is planned.
