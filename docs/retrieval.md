# Retrieval design

Stage 3 stores transcript, OCR and visual-description text in one `search_documents`
table. Text is normalized with Unicode NFKC and converted to English word tokens plus
Chinese character and bigram tokens. PostgreSQL `simple` full-text search handles the
keyword branch; pgvector cosine search handles the semantic branch.

Transcript and OCR searches use both branches. Visual-description searches use vectors.
Each branch retrieves up to `max(20, limit * 4)` candidates, capped by configuration.
Reciprocal Rank Fusion combines ranks with `k=60`; raw full-text and cosine scores are
never added together.

Time ranges are half-open intervals. A document overlaps a query when its start is before
the query end and its end is after the query start. Context expansion is clamped to the
video duration.

## Embedding spaces

An embedding space hash contains the profile name, gateway kind, endpoint host, configured
model and vector dimensions. It never contains the API key. Vector queries only compare
documents in the current space. Changing a model or endpoint therefore requires
`evistream retrieval-index <video-id> --force`.

Media preprocessing remains independent from external Embedding availability. It writes
searchable text first; indexing is an explicit and idempotent operation until Stage 6 adds
asynchronous dispatch.
