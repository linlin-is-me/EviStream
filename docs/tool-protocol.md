# Tool protocol

Every investigation tool accepts `ToolRequest` and returns `ToolResult`. `ToolExecutor`
validates the Case and Requirement, creates a canonical request key and persists the
running and terminal states in `tool_runs`. Repeating the same request in one run returns
the stored terminal result.

Stage 3 registers eight tools: transcript, OCR and visual-caption search; clip extraction;
temporal expansion; neighboring segments; counter-evidence retrieval; and policy
requirement lookup. Search results identify their exact SearchDocument and Artifact.
Clip extraction uses a deterministic video-and-time key, so repeated requests reuse the
same Artifact.

An empty search result is successful. Keyword results accompanied by an unavailable
vector branch are partial. A visual-only query without an available vector branch fails
with `RETRIEVAL_VECTOR_UNAVAILABLE`.

`inspect_clip` creates media only. Stage 4 supplies the VLM inspection and evidence
interpretation layers.
