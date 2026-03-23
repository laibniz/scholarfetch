# ScholarFetch Agent Guide

ScholarFetch is a stateful research environment for agents.

Use it to build and refine a literature set over multiple MCP calls, not just to fetch isolated papers.

## What The Agent Should Optimize For
- start broad, then narrow aggressively
- preserve only high-value papers in a named collection
- use abstracts for cheap triage
- use full text only for shortlisted papers
- expand references and authors only when they improve topic coverage
- export a compact artifact for downstream synthesis

## Mental Model
ScholarFetch exposes a research tree:
- topic -> papers
- paper -> abstract
- paper -> full text
- paper -> references
- paper -> authors
- author -> papers

The `collection` parameter is the agent's working memory inside one MCP server process.

## Primary Tool Roles

### Discovery
- `scholarfetch_search`
- `scholarfetch_doi_lookup`
- `scholarfetch_author_candidates`

Use these to establish seed nodes.

### Traversal
- `scholarfetch_author_papers`
- `scholarfetch_references`

Use these to move through the literature graph.

### Reading
- `scholarfetch_abstract`
- `scholarfetch_article_text`

Use these to decide whether a paper deserves inclusion.

### Curation
- `scholarfetch_saved_add`
- `scholarfetch_saved_list`
- `scholarfetch_saved_remove`
- `scholarfetch_saved_clear`
- `scholarfetch_saved_export`

Use these to keep a coherent reading list and hand off the result.

## Recommended Agent Loop
1. Run `scholarfetch_search` with a topic query.
2. Save obviously relevant papers with `scholarfetch_saved_add`.
3. For ambiguous authors, run `scholarfetch_author_candidates`.
4. Expand the strongest author nodes with `scholarfetch_author_papers`.
5. Read abstracts with `scholarfetch_abstract`.
6. Read full text with `scholarfetch_article_text` only for shortlisted papers.
7. Expand references with `scholarfetch_references` when a paper looks central or foundational.
8. Revisit the saved list with `scholarfetch_saved_list`.
9. Remove weak papers with `scholarfetch_saved_remove`.
10. Export with `scholarfetch_saved_export`.

## How To Use Collections
Use one stable `collection` name per research thread.

Good examples:
- `default`
- `graph-ml-review`
- `ai-marketing-core`
- `legal-rag-refs`

Do not mix unrelated topics in one collection.

## Input Strategy

### Best Add Path
When a tool already returned one paper object, prefer:
- `paper_json`

This avoids re-resolution ambiguity and preserves the selected record.

### Author Expansion
If you only have an author name:
1. call `scholarfetch_author_candidates`
2. choose the right `candidate_index`
3. call `scholarfetch_author_papers`

### Paper Reading
For one paper:
1. start with `scholarfetch_abstract`
2. if still relevant, call `scholarfetch_article_text`
3. if foundational, call `scholarfetch_references`

## Export Strategy

### `citations`
Use when you need:
- a lightweight bibliography
- quick reporting
- citation-style output

### `abstracts`
Use when you need:
- fast synthesis
- ranking and clustering
- a compact corpus for another agent

### `bib`
Use when you need:
- bibliography tooling
- citation manager interoperability

### `fulltext`
Use when you need:
- deep synthesis
- quote extraction
- methodology comparison
- evidence tracing

If you want citation context too, set:
- `include_references=true`

## Good Agent Behavior
- save early
- prune often
- prefer breadth before depth
- revisit the saved list after every major branch expansion
- stop expanding once marginal papers stop improving coverage

## Example Session Plan
1. Search `graph neural networks literature review`.
2. Save 5 to 10 promising papers.
3. Read abstracts for all saved papers.
4. Pick the top 2 to 3 papers.
5. Expand their references.
6. Expand one or two key authors.
7. Remove noise.
8. Export `abstracts` or `fulltext`.

## Failure Handling
- no abstract: keep the paper only if metadata or references still make it useful
- no full text: use abstract + references + citation metadata
- ambiguous author: resolve with `scholarfetch_author_candidates` before expanding papers
- duplicate papers across engines: prefer the resolved record returned by ScholarFetch rather than trying to deduplicate again client-side

## End State
A good ScholarFetch agent session ends with:
- one coherent saved collection
- a small number of genuinely useful papers
- an export artifact ready for a synthesis or writing step
