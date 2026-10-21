## ADDED Requirements

### Requirement: Authentication
The system SHALL authenticate web UI and API users with `EMAIL` and `PASSWORD` taken from the environment, and SHALL authenticate agents with `MCP_TOKEN`. Credentials SHALL NOT be stored in code, logs or responses.

#### Scenario: Valid login
- **WHEN** a user submits the configured email and password
- **THEN** a session or token is issued and access to the web UI and API is granted

#### Scenario: Invalid credentials
- **WHEN** the email, password or token is wrong
- **THEN** the request is rejected with an authorization error and no session is created

#### Scenario: Missing credentials
- **WHEN** a protected endpoint is called without credentials
- **THEN** the system responds with an authorization error

#### Scenario: Agent token
- **WHEN** an agent calls search or context with a valid `MCP_TOKEN` and a `project_id`
- **THEN** the request is accepted and scoped to that project

#### Scenario: Unconfigured credentials
- **WHEN** `EMAIL`, `PASSWORD` or `MCP_TOKEN` are not set at startup
- **THEN** the service refuses to authenticate anyone and reports a clear configuration error

### Requirement: Project management
The system SHALL let the owner create, list, view, rename and delete projects. Project names SHALL be non-empty and unique.

#### Scenario: Create project
- **WHEN** the owner creates a project with a valid name
- **THEN** it appears in the project list with a unique id and creation time

#### Scenario: Empty or duplicate name
- **WHEN** the owner creates or renames a project with an empty or already used name
- **THEN** the request is rejected with a validation error and nothing changes

#### Scenario: Rename project
- **WHEN** the owner renames a project
- **THEN** the new name is shown everywhere and the project id stays the same

#### Scenario: Delete project
- **WHEN** the owner deletes a project
- **THEN** its sources, versions, keys, jobs and index entries are removed and it disappears from the list

#### Scenario: Unknown project
- **WHEN** a request references a project id that does not exist
- **THEN** the system responds with a not-found error

### Requirement: API keys
The system SHALL issue API keys per project, show the secret exactly once, store only its hash and a short prefix, and allow revocation.

#### Scenario: Issue key
- **WHEN** the owner issues a key for a project
- **THEN** the full secret is returned once and later listings show only the prefix, creation time and status

#### Scenario: Revoked key
- **WHEN** a request uses a revoked key
- **THEN** it is rejected with an authorization error

#### Scenario: Key scope
- **WHEN** a key of project A is used to access project B
- **THEN** the request is rejected

#### Scenario: Unknown key
- **WHEN** a request uses a key that was never issued
- **THEN** it is rejected without revealing whether the key format was valid

### Requirement: Knowledge sources
The system SHALL accept files, plain text and links as knowledge sources inside a project, and SHALL allow viewing, editing and deleting them. Supported file types SHALL be `.txt`, `.md` and `.pdf`, with a configurable upload size limit (default 10 MB).

#### Scenario: Add text
- **WHEN** the user adds text with a title
- **THEN** a source with version 1 and status `pending` is created, an indexing job is enqueued, and the status becomes `indexed` after processing

#### Scenario: Add file
- **WHEN** the user uploads a supported file within the size limit
- **THEN** a source is created from its extracted text and indexed asynchronously

#### Scenario: Add link
- **WHEN** the user adds a reachable URL
- **THEN** its content is fetched, a source is created and indexed asynchronously

#### Scenario: Unreachable link
- **WHEN** a link cannot be fetched
- **THEN** the source is marked `failed` with a readable error message

#### Scenario: Unsupported file
- **WHEN** a file of an unsupported type is uploaded
- **THEN** the upload is rejected with a clear error and no source is created

#### Scenario: File too large
- **WHEN** an uploaded file exceeds the size limit
- **THEN** the upload is rejected with a clear error

#### Scenario: View source
- **WHEN** the user opens a source
- **THEN** its title, kind, status, current version and content are shown

### Requirement: Versioning
The system SHALL keep versions of a source. Each edit SHALL create a new version, and only the latest version SHALL participate in search.

#### Scenario: Edit source
- **WHEN** a source is edited
- **THEN** version N+1 is created, previous versions stay listed and the source is reindexed

#### Scenario: List versions
- **WHEN** the user opens the version history of a source
- **THEN** all versions are shown with number and creation time

#### Scenario: Search uses latest version
- **WHEN** a source has several versions
- **THEN** search results come only from the latest version

#### Scenario: Delete source
- **WHEN** a source is deleted
- **THEN** its versions and index entries are removed and it no longer appears in search results

### Requirement: Job queue and worker
The system SHALL process indexing in the background through a PostgreSQL job queue that selects jobs with `FOR UPDATE SKIP LOCKED`, retries failed jobs and supports reindexing.

#### Scenario: Concurrent workers
- **WHEN** several workers poll the queue
- **THEN** each job is taken by exactly one worker

#### Scenario: Retry on failure
- **WHEN** a job fails
- **THEN** its `attempts` counter increases and it is retried until the attempt limit, after which the source is marked `failed`

#### Scenario: Reindex
- **WHEN** an index diverges from PostgreSQL
- **THEN** a reindex run enqueues jobs that rebuild the index from PostgreSQL data

### Requirement: Search and context
The system SHALL provide search and context endpoints that require an explicit `project_id` and return only that project's knowledge. Search SHALL combine vector and full-text results.

#### Scenario: Missing project_id
- **WHEN** a search or context request has no `project_id`
- **THEN** an error is returned and no search is performed

#### Scenario: Project isolation
- **WHEN** searching in project A
- **THEN** no results from project B are returned

#### Scenario: Result content
- **WHEN** search returns results
- **THEN** each result contains the source id, title, version, text fragment and score

#### Scenario: Context for agent
- **WHEN** an agent requests context for a query in a project
- **THEN** the top fragments of that project are returned in a form suitable for a prompt

#### Scenario: Vector index unavailable
- **WHEN** the vector index is down
- **THEN** results come from full-text search

#### Scenario: Empty result
- **WHEN** nothing matches the query
- **THEN** an empty list is returned without an error

### Requirement: Data access layering
The system SHALL access data only through repository interfaces (`ProjectRepository`, `ApiKeyRepository`, `SourceRepository`, `VersionRepository`, `JobRepository`) defined as Protocols in `vector_core`. Routers SHALL call services, and services SHALL NOT contain SQL.

#### Scenario: Service tests
- **WHEN** a service is tested
- **THEN** it runs against in-memory repository implementations without a database

#### Scenario: SQL isolation
- **WHEN** the code base is inspected
- **THEN** SQL appears only in repository implementations and migrations

### Requirement: Web UI
The system SHALL provide a consistent, clear web UI with "Projects" and "Knowledge" pages built on shared components and a single API client.

#### Scenario: States
- **WHEN** data is loading, empty or failed
- **THEN** the UI shows a loading, empty or error state respectively

#### Scenario: Login
- **WHEN** an unauthenticated user opens the UI
- **THEN** a login form is shown and the rest of the UI is not accessible

#### Scenario: Manage projects
- **WHEN** the user is on the Projects page
- **THEN** they can create, rename and delete projects, with confirmation before deletion

#### Scenario: Manage knowledge
- **WHEN** a user opens a project
- **THEN** they can add text, files and links, edit and delete sources, view versions, and see each source's status

#### Scenario: Manage keys
- **WHEN** a user opens the keys section of a project
- **THEN** they can issue a key, copy its secret once, and revoke keys

#### Scenario: Test search
- **WHEN** a user enters a query on the project page
- **THEN** search results of that project are shown

### Requirement: Reproducibility and secrets
The system SHALL pin all dependency and image versions, SHALL NOT use `latest`, and SHALL read secrets only from the environment populated from the vault. The stack SHALL start with `docker compose up -d` after `.env` is filled with `EMAIL`, `PASSWORD` and `MCP_TOKEN`.

#### Scenario: Pinned versions
- **WHEN** dependency files and compose are inspected
- **THEN** every version is fixed and none is `latest`

#### Scenario: Secrets not committed
- **WHEN** the repository is inspected
- **THEN** no secret values appear in code, YAML, `.env.example`, logs or commits

#### Scenario: One-command start
- **WHEN** the owner copies `.env.example` to `.env`, fills the three variables and runs `docker compose up -d`
- **THEN** the full stack starts without further manual configuration
