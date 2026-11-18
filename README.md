![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

# Git CMS Engine

A lightweight, secure, and asynchronous Git-backed Headless CMS Engine built with FastAPI, SQLAlchemy, and PostgreSQL.

The Git CMS Engine dynamically synchronizes Markdown content (such as knowledge base articles, write-ups, and technical documentation) from a public or private Git repository directly into a PostgreSQL database. It exposes structured REST APIs designed for integration with modern frontends, including Next.js, Astro, or Nuxt.

---

## Project Overview and Architecture

The system consists of the following technical components:

1. **FastAPI Backend**: Implements the REST API endpoints, parses incoming Git webhooks, coordinates repository synchronization, processes Markdown content, and resolves internal article links.
2. **PostgreSQL Database**: Maintains the structured article metadata, self-referential article relations, and publication metrics under the isolated database schema `cms`. It also stores daily-synchronized Zotero sources under the `zotero` schema.
3. **Git-Sync Pipeline**: Performs a fully automated clone, pull, and reset workflow. It converts the repository state into a validated on-disk cache, processes frontmatter metadata, and executes database upserts.
4. **Zotero Integration**: Integrates Zotero citation metadata using relational schema mapping. It maps cite-keys in Markdown files to the database table `zotero.sources`, allowing bibliographies to be populated dynamically.
5. **Next.js Frontend / API Proxy Integration**: Interacts with the backend using catch-all dynamic routing and route parameters. The frontend proxies API requests to load compiled Markdown and dynamically render interactive page sidebars and content graphs.

```mermaid
flowchart TD
    Repo["Git Repository<br/><small>(Markdown + YAML Frontmatter)</small>"]
    FastAPI["FastAPI CMS Engine<br/><small>(Git Sync, Parser & Link Resolver)</small>"]
    DB[("PostgreSQL Database<br/><small>(Schemas: cms, zotero)</small>")]
    Disk["Processed Volume Disk<br/><small>(/app/data/content)</small>"]
    NextUI["Next.js UI Frontend<br/><small>(Interactive Sidebar & Relation Graph)</small>"]

    Repo -->|"Git Webhook (POST /sync)"| FastAPI
    FastAPI -->|"Write Metadata"| DB
    FastAPI -->|"Static Serve"| Disk

    NextUI -->|"Fetch JSON"| DB
    NextUI -->|"Load Markdown"| Disk
```

---

## Key Features

- **Modular Git-Based CMS Engine**: Uses Git as the single source of truth for versioning, revisions, and content management.
- **Dual-Volume Workflow**: Utilizes a dual-volume workflow distinguishing between raw cloned Git repository data (`CMS_RAW_DATA_PATH`) and processed, safe markdown files (`CMS_PROCESSED_DATA_PATH`).
- **Metadata Upserting**: Implements a robust delta-comparison/upsert process that matches articles by repository-relative file path, updates only changed fields, tracks update timestamps, and clears deleted or unpublished records.
- **Link Resolution**: Automatically parses internal Markdown links (such as relative links and Wiki-links `[[Target]]`) and resolves them against database slugs/routes, generating correct frontend routing URLs.
- **Wiki-Link Parsing**: Robust parsing of double-bracket Obsidian-style Wiki-links (`[[slug|display_text]]` and `[[slug]]`). Automatically trims whitespace, escapes HTML against XSS vulnerabilities, and converts links into standard HTML anchor (`<a>`) tags using configurable base paths or custom resolver functions.
- **Relational Frontmatter Mapping**: Automatically parses `parent`, `children` (or `child`), and `related` list fields in YAML frontmatter, coercing values to trimmed kebab-case slugs and persisting them to PostgreSQL `text[]` array columns for building article link-graphs and breadcrumbs.
- **Citation Mapping**: Resolves custom frontmatter citations against PostgreSQL `zotero.sources`, formatting standard, readable references for page bibliographies.

---

### Wiki-Link Parsing Syntax & Features

The link resolver service provides a dedicated `parse_wiki_links()` function to transform double-bracket Obsidian-style Wiki-links into standard HTML anchor (`<a>`) tags.

#### Supported Syntax
1. **With Pipe Separator (`[[slug|display_text]]`)**:
   - `[[my-custom-slug|Mein angezeigter Titel]]` -> `<a href="/my-custom-slug">Mein angezeigter Titel</a>`
2. **Without Pipe Separator (`[[slug]]`)**:
   - `[[my-custom-slug]]` -> `<a href="/my-custom-slug">my-custom-slug</a>` (falls back to the slug)

#### Key Capabilities
- **Whitespace Handling**: Automatically trims leading and trailing whitespace inside double brackets (e.g., `[[ slug | Text ]]` -> `<a href="/slug">Text</a>`).
- **Slugs with Slashes & Hyphens**: Supports deep path slugs and kebab-case identifiers (e.g., `[[security/identity/oauth2-vs-oidc]]`).
- **XSS Sanitization & Escaping**: All inner display texts and href attributes are safely escaped using `html.escape` to prevent XSS attacks.
- **Configurable Base Path**: Supports custom string path prefixes (e.g., `/security/identity/`) or custom URL resolver callbacks (`Callable[[str, str], str]`).

#### Code Examples
```python
from services.link_resolver import parse_wiki_links

# 1. Standard usage with default base path ("/")
text = "Check out [[security/oauth2|OAuth 2.0 Guide]] for details."
html_out = parse_wiki_links(text)
# Output: Check out <a href="/security/oauth2">OAuth 2.0 Guide</a> for details.

# 2. Custom base path prefix
html_out = parse_wiki_links(
    "Check [[oauth2-vs-oidc|OAuth vs OIDC]]",
    base_path="/security/identity/"
)
# Output: Check <a href="/security/identity/oauth2-vs-oidc">OAuth vs OIDC</a>

# 3. Custom resolver function
def custom_resolver(slug: str, display: str) -> str:
    return f"https://docs.example.com/kb/{slug}"

html_out = parse_wiki_links("[[oauth2|OAuth Spec]]", base_path=custom_resolver)
# Output: <a href="https://docs.example.com/kb/oauth2">OAuth Spec</a>
```
- **Media Asset Serving**: Recursively copies assets (such as images and screenshots) from raw cloned repositories into statically mounted directories, resolving internal Markdown asset paths cleanly.

---

## Directory Structure

```
git-cms-engine/
├── api/                       # FastAPI Endpoints and Routing Layers
│   ├── __init__.py
│   ├── articles.py            # Article listing, querying, and routing APIs
│   ├── dependencies.py        # Database session and security dependencies
│   ├── router.py              # Aggregation router for API endpoints
│   ├── sources.py             # Zotero / citation source querying APIs
│   └── sync.py                # Git webhook integration and database synchronization API
├── docs/                      # Technical Documentation
│   ├── API.md                 # Detailed REST contract specifications
│   └── CHANGELOG.md           # Semantic version history tracking
├── init-scripts/              # Database Initialization Scripts
│   └── 01-init.sql            # Core SQL script to create schemas (cms, zotero)
├── models/                    # SQLAlchemy Database Models
│   └── article.py             # Relational mapping for articles, sources, and relations
├── repository/                # Data Persistence and Database Operations
│   ├── __init__.py
│   ├── article_repository.py  # Bulk upsert and relational link-graph resolution
│   └── database.py            # Database engine bootstrapping and session lifecycle
├── schemas/                   # Pydantic Schemas and Validation Models
│   └── article.py             # Validation schemas for frontmatter and API responses
├── services/                  # Core Business and Synchronization Logic
│   ├── __init__.py
│   ├── exceptions.py          # Domain-specific and security exceptions
│   ├── git_sync.py            # Repository cloning, pulling, and HMAC validation
│   ├── link_resolver.py       # Inline Markdown and Wiki-link routing engine
│   ├── parser.py              # Frontmatter extraction and path-traversal validation
│   └── processor.py           # Recursive filesystem synchronization and stale-cleanup
├── tests/                     # Automated Test Suites
├── .dockerignore              # Docker build context exclusion rules
├── .env.example               # Template environment configuration (secrets omitted)
├── .gitignore                 # Version control exclusions
├── config.py                  # Environment-backed central configuration (Pydantic BaseSettings)
├── conftest.py                # Pytest global fixtures and configurations
├── Dockerfile                 # Multi-stage production container file (Python 3.12 slim)
├── docker-compose.example.yml # Template Compose file for reproducible deployment
├── docker-compose.yml         # Standalone Compose stack with FastAPI and PostgreSQL
├── main.py                    # Application entry point and local synchronization routine
└── requirements.txt           # Python dependency specifications
```

---

## Prerequisites

- **Python**: Version 3.12 or higher.
- **PostgreSQL**: Version 15 or higher with schema isolation capabilities.
- **Git**: Installed and accessible on the system environment variable PATH.
- **Docker**: Docker Engine and Docker Compose for containerized deployments.

### Core Environment Variables

Ensure the following variables are configured in your deployment environment or a local `.env` file:

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `CMS_USE_LOCAL_CONTENT` | `bool` | `False` | Skips Git operations and operates directly on local files. |
| `CMS_GIT_URL` | `str` | *Required* | Clone URL for the source content Git repository. |
| `CMS_GIT_PAT` | `str` | `None` | Personal Access Token used for private repository authentication. |
| `CMS_GIT_BRANCH` | `str` | `"main"` | Targeted repository branch to check out and synchronize. |
| `CMS_REPO_LOCAL_PATH` | `Path` | `"./.cms_repo_cache"`| Directory for holding the local Git repository working copy. |
| `CMS_RAW_DATA_PATH` | `Path` | `"/app/raw_data"` | Working directory containing the raw Git clone. |
| `CMS_PROCESSED_DATA_PATH`| `Path` | `"/app/data"` | Directory where parsed and processed markdown files are output. |
| `CMS_ALLOWED_FOLDERS` | `list[str]`| `[]` | Optional relative folders under `content/` to limit processing. |
| `CMS_SYNC_SECRET` | `str` | `"changeme"` | Shared secret key for authenticating `/sync` webhooks. |
| `CMS_DATABASE_URL` | `str` | *Required* | SQLAlchemy connection URL for PostgreSQL. |
| `CMS_DB_SCHEMA` | `str` | `"cms"` | Dedicated PostgreSQL schema for stored article metadata. |

---

## Setup and Deployment Guide

### Local Development Setup (Manual)

1. **Clone the Engine Repository**:
   ```bash
   git clone https://github.com/KevinKrummsdorf/git-cms-engine.git
   cd git-cms-engine
   ```

2. **Initialize a Virtual Environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Configure Local Environment**:
   Create an `.env` file in the root directory (based on `.env.example`):
   ```env
   CMS_USE_LOCAL_CONTENT=True
   CMS_DATABASE_URL=postgresql+psycopg2://postgres:password@localhost:5432/cms_test_db
   CMS_SYNC_SECRET=local-development-secret
   CMS_RAW_DATA_PATH=./raw_data
   CMS_PROCESSED_DATA_PATH=./data
   ```

5. **Execute the Application**:
   ```bash
   uvicorn main:app --reload --host 127.0.0.1 --port 8000
   ```

---

### Local Development Setup (Docker Compose)

The repository provides a pre-configured, standalone Docker Compose stack comprising a PostgreSQL database (with automated schema initialization) and the FastAPI backend.

1. **Configure Environment Variables**:
   Copy the example environment template and adjust values if needed:
   ```bash
   cp .env.example .env
   ```

2. **Start the Multi-Container Stack**:
   ```bash
   docker compose up --build -d
   ```

3. **Verify running containers**:
   ```bash
   docker compose ps
   ```

4. **Monitor API service output**:
   ```bash
   docker compose logs -f cms-backend
   ```

---

### Production Deployment

In a production environment, deployment is recommended using the multi-stage `Dockerfile` with high-performance production parameters.

1. **Build the Production Image**:
   ```bash
   docker build -t git-cms-engine:latest -f Dockerfile .
   ```

2. **Execute the Container**:
   ```bash
   docker run -d \
     --name cms_backend \
     --restart always \
     -p 8000:8000 \
     -v cms_raw_data:/app/raw_data \
     -v cms_processed_data:/app/data \
     -e CMS_GIT_URL="https://github.com/your-org/docs.git" \
     -e CMS_SYNC_SECRET="highly-secure-production-secret" \
     -e CMS_DATABASE_URL="postgresql+psycopg2://db_user:db_password@prod-db:5432/production_db" \
     git-cms-engine:latest
   ```

---

## API Documentation Summary

### Webhook Synchronization
* **Endpoint**: `POST /api/v1/cms/sync`
* **Authentication**: Required header `X-CMS-Sync-Secret` (or GitHub HMAC-SHA256 signature `X-Hub-Signature-256`).
* **Description**: Pulls the newest commits, parses metadata, resolves inline links, updates database configurations, and handles the stale cleanup.
* **Response (200 OK)**:
  ```json
  {
    "status": "success",
    "message": "Content synchronization and database sync completed successfully",
    "processed_files": {
      "total": 15,
      "created": 2,
      "updated": 5,
      "deleted": 1,
      "ignored": 7
    },
    "timestamp": "2026-07-24T12:00:00.000000+00:00"
  }
  ```

### Article Indexing
* **Endpoint**: `GET /api/v1/cms/articles`
* **Query Parameters**:
  - `published` (bool): Filter by publication state (Default: `true`).
  - `category` (str, optional): Filter by article category.
  - `tag` (str, optional): Filter by article tag.
  - `limit` (int): Pagination ceiling (Default: `20`).
* **Response (200 OK)**:
  ```json
  {
    "count": 1,
    "articles": [
      {
        "id": 1092837482,
        "title": "Introduction to Network Security Concepts",
        "slug": "network-security-intro",
        "route": "security/network-security-intro",
        "category": "security",
        "subcategory": "basics",
        "description": "Comprehensive guide introducing foundational concepts of security.",
        "tags": ["Security", "Network", "Basics"],
        "date": "2026-01-15",
        "updated": "2026-02-10",
        "published": true,
        "has_math": true,
        "has_charts": false
      }
    ]
  }
  ```

### Fetch Article by Route
* **Endpoint**: `GET /api/v1/cms/articles/by-route`
* **Query Parameters**:
  - `route` (str, required): The URL-encoded path representing the target article.
* **Response (200 OK)**:
  ```json
  {
    "metadata": {
      "id": 1092837482,
      "title": "Introduction to Network Security Concepts",
      "slug": "network-security-intro",
      "route": "security/network-security-intro",
      "category": "security",
      "subcategory": "basics",
      "description": "Comprehensive guide introducing foundational concepts of security.",
      "tags": ["Security", "Network", "Basics"],
      "date": "2026-01-15",
      "updated": "2026-02-10",
      "published": true,
      "has_math": true,
      "has_charts": false
    },
    "content": {
      "raw_markdown": "# Introduction to Network Security Concepts\n\nSecurity is paramount...",
      "reading_time_minutes": 4
    },
    "sources": [
      {
        "cite_key": "Kappes2013",
        "title": "Netzwerkzeitalter: Grundlagen und Sicherheit",
        "authors": ["Martin Kappes"],
        "publisher": null,
        "year": 2013,
        "url": "https://doi.org/10.1007/978-3-8348-2070-9"
      }
    ],
    "internal_links": {
      "parent": null,
      "children": [],
      "related_articles": []
    }
  }
  ```

### Fetch Citation Sources
* **Endpoint**: `GET /api/v1/cms/sources`
* **Query Parameters**:
  - `cite_key` (str, optional): Filter by a specific citation key.
  - `article_id` (int, optional): Filter for sources linked to a specific article.
* **Response (200 OK)**:
  ```json
  {
    "total": 1,
    "sources": [
      {
        "cite_key": "Kappes2013",
        "title": "Netzwerkzeitalter: Grundlagen und Sicherheit",
        "authors": ["Martin Kappes"],
        "publisher": null,
        "year": 2013,
        "url": "https://doi.org/10.1007/978-3-8348-2070-9"
      }
    ]
  }
  ```

### System Health
* **Endpoint**: `GET /health`
* **Response (200 OK)**:
  ```json
  {
    "status": "ok"
  }
  ```

---

## Maintenance and Troubleshooting

### Common Setup Issues

- **Database Connection Failures**: Ensure PostgreSQL is running, listens on the correct host/port, and that database credentials match `CMS_DATABASE_URL` exactly.
- **Directory Permissions**: The application processes directory deletion and synchronization dynamically. Ensure the server user holds read and write permissions for `/app/raw_data` and `/app/data`.

### Git Sync Authentication Errors

- **401 Unauthorized Response**: Verify the value of `X-CMS-Sync-Secret` (or HMAC validation keys) matches the configured `CMS_SYNC_SECRET` environment setting.
- **Git Failures (Private Repositories)**: Ensure the Personal Access Token (`CMS_GIT_PAT`) is defined and has sufficient scopes (`repo` read permission) to pull from private hosts.

### Database Schema Initialization and Migrations

The SQLAlchemy database layer automatically provisions schemas and tables on startup (`Base.metadata.create_all(bind=engine)`). If structural schema updates are introduced:
1. Log in to your database administration console.
2. Execute custom migrations, or drop existing tables in the isolated schema `cms` to trigger a safe rebuild:
   ```sql
   DROP SCHEMA cms CASCADE;
   CREATE SCHEMA cms;
   ```

### Container Management Commands

- **Restart the Stack**:
  ```bash
  docker compose restart
  ```
- **Stop and Clear Volumes**:
  ```bash
  docker compose down -v
  ```
- **Inspect SQL Initialization Output**:
  ```bash
  docker compose logs postgres
  ```

---

## License

This software is distributed under the [MIT License](LICENSE). Feel free to use, modify, and distribute the engine in commercial and open-source applications.
