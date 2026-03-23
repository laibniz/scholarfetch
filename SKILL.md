# ScholarFetch Research Skill

Use ScholarFetch when the task is academic discovery, citation expansion, corpus building, or literature-grounded synthesis.

## Objective
Move from an initial topic, DOI, author, or seed paper to a curated reading list and an export artifact that another agent or human can use immediately.

## Available Starting Points
- topic keywords
- one DOI
- one known author
- one paper returned by another tool

## Core Operating Pattern

### 1. Discover
Use:
- `scholarfetch_search`
- `scholarfetch_doi_lookup`
- `scholarfetch_author_candidates`

Goal:
- create the first set of candidate papers or authors

### 2. Triage
Use:
- `scholarfetch_abstract`
- `scholarfetch_doi_lookup`

Goal:
- reject weak candidates cheaply
- avoid unnecessary full-text retrieval

### 3. Expand
Use:
- `scholarfetch_author_papers`
- `scholarfetch_references`

Goal:
- traverse sideways through authors
- traverse backward through citations
- keep the search close to the topic

### 4. Curate
Use:
- `scholarfetch_saved_add`
- `scholarfetch_saved_list`
- `scholarfetch_saved_remove`
- `scholarfetch_saved_clear`

Goal:
- maintain a deliberate working set
- separate high-value papers from noisy candidates

### 5. Export
Use:
- `scholarfetch_saved_export`

Goal:
- produce a stable artifact for synthesis, writing, or further analysis

## Decision Heuristics
- use `abstract` before `article_text`
- use `references` when the paper looks foundational
- use `author_papers` when the paper looks representative of an author's line of work
- save papers as soon as they appear clearly relevant
- remove papers once they stop contributing to coverage

## Collection Discipline
Always choose and reuse one `collection` name per topic branch.

Examples:
- `default`
- `ml-governance-review`
- `digital-health-citations`

The collection is the agent's session memory.

## Recommended Minimal Workflow
1. Search a topic.
2. Save 5 to 15 plausible papers.
3. Read abstracts for all saved papers.
4. Expand references for the strongest 2 to 3 papers.
5. Expand authors for the strongest 1 to 2 papers.
6. Remove weak or redundant papers.
7. Export `abstracts` or `fulltext`.

## Export Guidance

### `citations`
Best for:
- summaries
- notes
- bibliography sections

### `abstracts`
Best for:
- topic mapping
- clustering
- fast synthesis

### `bib`
Best for:
- citation managers
- external bibliography tooling

### `fulltext`
Best for:
- deep review
- methods comparison
- evidence extraction
- grounded synthesis

Use `include_references=true` when the export should also preserve citation context.

## Quality Bar
A good ScholarFetch run should produce:
- a coherent collection
- explicit inclusion/exclusion decisions
- a compact export artifact
- enough full text to support real synthesis without over-collecting
