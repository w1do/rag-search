## Context

See proposal.md. Monorepo: `apps/api`, `apps/worker`, `apps/web`, `vector_core`, `vector_cli`. PostgreSQL is the source of truth; Qdrant and Typesense are derived indexes.

## Goals / Non-Goals

- Goals: layered code, repository-based data access, pleasant UI, single implementation pass.
- Non-Goals: n8n, external SaaS, manual setup beyond `.env`.

## Decisions

- Layers: router → service → repository. Services depend on Protocol interfaces from `vector_core` and never on SQL. Alternative (direct SQL in routers) rejected: untestable.
- Repositories: ProjectRepository, ApiKeyRepository, SourceRepository, VersionRepository, JobRepository; SQL implementations in `vector_core`, in-memory fakes for tests.
- Data schema: `projects(id, name, created_at)`, `api_keys(id, project_id, hash, prefix, revoked_at)`, `sources(id, project_id, kind, title, status, current_version)`, `source_versions(id, source_id, version, content_ref, created_at)`, `ingestion_jobs(id, project_id, type, payload, status, attempts, max_attempts, available_at, leased_until, leased_by, last_error, created_at, updated_at)`. `payload` is JSONB; statuses: `pending`, `leased`, `succeeded`, `failed`. Keys stored hashed.
- Jobs: PostgreSQL queue consumed by the worker (`FOR UPDATE SKIP LOCKED`); indexing is asynchronous.
- Search: `project_id` required at service level; Qdrant first, Typesense fallback.
- Auth: `EMAIL`/`PASSWORD` for UI/API, `MCP_TOKEN` for agents, all from env.
- UI: React pages "Projects" and "Knowledge", shared components, loading/empty/error states, API client module.

## Risks / Trade-offs

- [Large single pass] → tasks grouped, each group verified.
- [Index drift from PostgreSQL] → reindex via jobs; PostgreSQL authoritative.

## Migration Plan

Add schema migrations; rollback by reverting the migration.
