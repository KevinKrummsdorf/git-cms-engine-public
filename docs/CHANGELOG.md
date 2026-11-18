# Changelog

All notable changes to the Git CMS Engine project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-11-18

### Added
- **Citation key deduplication** (`services/processor.py`, `api/articles.py`): Case-insensitive, order-preserving deduplication (`deduplicate_cite_keys`) for frontmatter `related_sources` and `relevance` entries to prevent PostgreSQL `UniqueViolation` errors on `(article_id, source_key)` during sync.
- **DOI filtering (`10.xxxx/...`)** (`services/processor.py`, `api/articles.py`): Added `is_doi` detection (supporting bare `10.` prefixes, `doi:`, and `https://doi.org/` URLs) to automatically skip raw DOIs during database synchronization and API responses.
- **Structured observability logging & sync warnings**: Added flushed `[PROCESSOR INFO]` stdout log lines for skipped DOIs and duplicate `cite_key` entries (enabling Loki/Grafana alerting integration) and exposed collected warnings in the `POST /api/v1/cms/sync` response payload (`processed_files.warnings`).
- **Unit test suite** (`tests/test_dedup_cite_keys.py`): Added comprehensive unit tests for `is_doi`, case-insensitive cite-key deduplication, warning collection, and silent execution during read-only API requests.

## [1.0.0] - 2026-11-04

### Added
- Extended PostgreSQL database model (`ArticleModel`) and YAML frontmatter schema (`FrontmatterSchema`) with relational array fields: `parent`, `children` (supporting `child` alias), and `related`.
- Added automatic slug normalization (trimming and kebab-case conversion) for frontmatter relationship arrays during database synchronization and relation rebuilding passes.
- Obsidian-style Wiki-link parser function (`parse_wiki_links`) supporting pipe separation (`[[slug|display_text]]`) and fallback syntax (`[[slug]]`).
- Configurable `base_path` / `basePath` option allowing string path prefixes (e.g., `/security/identity/`) or custom URL resolver callbacks (`Callable[[str, str], str]`).
- Automatic whitespace trimming, XSS HTML escaping for display text and attributes, and multi-occurrence global matching in Markdown content.