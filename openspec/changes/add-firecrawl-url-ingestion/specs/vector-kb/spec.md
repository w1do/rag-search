## MODIFIED Requirements

### Requirement: Knowledge sources
The system SHALL accept files, plain text and links as knowledge sources inside a project, and SHALL allow viewing, editing and deleting them. Supported file types SHALL be `.txt`, `.md` and `.pdf`, with a configurable upload size limit (default 10 MB). Link content SHALL be extracted only through Firecrawl as defined by the `link-extraction` capability; without `FIRECRAWL_API_KEY` links SHALL be rejected.

#### Scenario: Add text
- **WHEN** the user adds text with a title
- **THEN** a source with version 1 and status `pending` is created, an indexing job is enqueued, and the status becomes `indexed` after processing

#### Scenario: Add file
- **WHEN** the user uploads a supported file within the size limit
- **THEN** a source is created from its extracted text and indexed asynchronously

#### Scenario: Add link
- **WHEN** the user adds a reachable URL and `FIRECRAWL_API_KEY` is configured
- **THEN** its full page content is extracted as Markdown, a source is created and indexed asynchronously

#### Scenario: Link ingestion not configured
- **WHEN** the user adds a URL and `FIRECRAWL_API_KEY` is not configured
- **THEN** the request is rejected with a clear error and no source is created

#### Scenario: Unreachable link
- **WHEN** a link cannot be fetched or its content cannot be extracted
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
