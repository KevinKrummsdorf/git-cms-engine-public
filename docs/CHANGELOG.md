# Changelog

All notable changes to the Git CMS Engine project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Extended PostgreSQL database model (`ArticleModel`) and YAML frontmatter schema (`FrontmatterSchema`) with relational array fields: `parent`, `children` (supporting `child` alias), and `related`.
- Added automatic slug normalization (trimming and kebab-case conversion) for frontmatter relationship arrays during database synchronization and relation rebuilding passes.
- Obsidian-style Wiki-link parser function (`parse_wiki_links`) supporting pipe separation (`[[slug|display_text]]`) and fallback syntax (`[[slug]]`).
- Configurable `base_path` / `basePath` option allowing string path prefixes (e.g., `/security/identity/`) or custom URL resolver callbacks (`Callable[[str, str], str]`).
- Automatic whitespace trimming, XSS HTML escaping for display text and attributes, and multi-occurrence global matching in Markdown content.
