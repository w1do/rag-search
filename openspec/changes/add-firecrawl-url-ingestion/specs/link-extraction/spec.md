## ADDED Requirements

### Requirement: Firecrawl link extraction
The system SHALL extract the content of a link source only through the Firecrawl Scrape API (`POST {FIRECRAWL_BASE_URL}/v2/scrape` with `formats: ["markdown"]` and `onlyMainContent: true`) and SHALL store the returned Markdown, without truncation, as the source version with extension `md`. The request SHALL be authenticated with `Authorization: Bearer <FIRECRAWL_API_KEY>` and SHALL be bounded by `FIRECRAWL_TIMEOUT_SECONDS` (default 60).

#### Scenario: Full page content is indexed
- **WHEN** the owner adds a reachable URL and Firecrawl returns Markdown for it
- **THEN** a source of kind `url` is created whose version contains the complete Markdown returned by Firecrawl, `extracted_chars` equals the Markdown length, and the version is chunked and indexed asynchronously like any other source

#### Scenario: JavaScript-rendered page
- **WHEN** the URL points to a page whose content is rendered by JavaScript
- **THEN** the stored text contains the rendered content returned by Firecrawl, not only the static HTML shell

#### Scenario: Structure is preserved
- **WHEN** the page contains headings, lists or tables
- **THEN** the stored text keeps them as Markdown instead of a single whitespace-joined line

#### Scenario: Title from page metadata
- **WHEN** a URL is added without an explicit `title` and Firecrawl returns `metadata.title`
- **THEN** the source title is the page title; otherwise the URL-derived title is used

#### Scenario: Explicit title wins
- **WHEN** a URL is added with an explicit `title`
- **THEN** that title is used regardless of the page metadata

#### Scenario: Unsupported scheme
- **WHEN** a URL with a scheme other than `http` or `https` is added
- **THEN** the request is rejected with a validation error, no source is created and no request is sent to Firecrawl

### Requirement: Link ingestion requires Firecrawl
The system SHALL NOT fetch links with any built-in HTTP fetcher. When `FIRECRAWL_API_KEY` is empty or unset, the system SHALL reject adding a link with error code `link_ingestion_not_configured` (HTTP 503) and message `link ingestion requires FIRECRAWL_API_KEY`, SHALL NOT create a source and SHALL NOT send any request. Text, file and sync sources SHALL keep working without the key.

#### Scenario: No key configured
- **WHEN** `FIRECRAWL_API_KEY` is not set and a URL is added
- **THEN** the request fails with HTTP 503 and code `link_ingestion_not_configured`, no source or indexing job is created, and no request is sent to Firecrawl or to the URL

#### Scenario: Other sources without key
- **WHEN** `FIRECRAWL_API_KEY` is not set and a text or file source is added
- **THEN** the source is created and indexed as before

### Requirement: Firecrawl failures
The system SHALL mark a link source `failed` with a readable `error_message` when Firecrawl cannot deliver content, and SHALL NOT fall back to any other fetcher in that case.

#### Scenario: Out of credits
- **WHEN** Firecrawl responds with HTTP 402
- **THEN** the source is `failed` with a message stating that Firecrawl credits are exhausted

#### Scenario: Rate limited
- **WHEN** Firecrawl responds with HTTP 429
- **THEN** the source is `failed` with a message stating that the Firecrawl rate limit was hit

#### Scenario: Key rejected
- **WHEN** Firecrawl responds with HTTP 401 or 403
- **THEN** the source is `failed` with a message asking to check `FIRECRAWL_API_KEY`, without the key value

#### Scenario: Timeout or service error
- **WHEN** the Firecrawl request times out, cannot connect, or returns HTTP 5xx
- **THEN** the source is `failed` with a message stating that the link could not be fetched via Firecrawl

#### Scenario: Target page error
- **WHEN** Firecrawl returns `success: false`, or `metadata.statusCode` is 400 or higher
- **THEN** the source is `failed` with a message that includes the target page status code when it is known

#### Scenario: Empty content
- **WHEN** Firecrawl returns empty or whitespace-only Markdown
- **THEN** the source is `failed` with a message stating that no content was extracted, and no version is created

#### Scenario: Content over size limit
- **WHEN** the UTF-8 encoded Markdown exceeds `MAX_UPLOAD_BYTES`
- **THEN** the source is `failed` with the size-limit message and no version is created

#### Scenario: Retry after failure
- **WHEN** the owner re-posts a URL whose previous Firecrawl extraction failed
- **THEN** the extraction is retried on the same source

### Requirement: Firecrawl key protection
The system SHALL treat `FIRECRAWL_API_KEY` as a secret: it SHALL be read only from the process environment, and SHALL NOT appear in `Settings` repr, logs, error messages, API responses, stored source content, `.env.example` values or the repository.

#### Scenario: Error does not leak key
- **WHEN** any Firecrawl call fails
- **THEN** the stored `error_message` and log records do not contain the key value

#### Scenario: Settings repr
- **WHEN** `Settings` is printed or logged
- **THEN** the Firecrawl key value is not shown
