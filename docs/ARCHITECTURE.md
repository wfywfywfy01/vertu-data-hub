# Architecture

## Project boundary

Vertu Store Agent is one business project with two repositories:

- `vertu-data-hub`: data ingestion, catalog, chunking, embeddings, and retrieval.
- `vertu-store-agent`: store collection forms, ETL, agent APIs, and presentation.

The repositories remain independently deployable. The shared contract is the
database schema, source metadata, and documented retrieval behavior. No direct
import from one repository into the other.

## Data flow

```text
files / skills / databases
        |
        v
 connectors -> catalog -> ingestion -> PostgreSQL + pgvector
                                      |
                                      v
                              read-only retrieval
```

Structured data uses relational or JSONB storage. Text and image similarity use
pgvector. Every ingested record keeps source identity, metadata, and a stable
content hash so repeated syncs remain idempotent.

## Production boundary

The data hub does not expose a user-facing agent API. It supplies storage and
retrieval capabilities to the companion application through the database
contract and approved read paths.
