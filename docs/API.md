# API Documentation: CMS Engine Module

The CMS module operates as an independent API service. It receives Git-based webhooks, synchronizes Markdown files containing YAML frontmatter, stores structured metadata and Zotero sources in a relational PostgreSQL database, and exposes formatted JSON endpoints for modern frontends.

---

## 1. Webhook and Git-Sync Engine

### `POST /api/v1/cms/sync`

Receives a Git webhook trigger, executes a Git fetch or clone process, parses all Markdown files and frontmatter metadata, resolves internal markdown and wiki-links, and updates the PostgreSQL database by executing a safe transaction-controlled upsert.

* **Authentication**: Requires the header `X-CMS-Sync-Secret` (shared secret matching `CMS_SYNC_SECRET`) or the standard GitHub Webhook HMAC-SHA256 signature header `X-Hub-Signature-256`.
* **Trigger**: Manual triggers, continuous integration webhooks, or push events.

#### Headers

```http
Content-Type: application/json
X-CMS-Sync-Secret: <YOUR_SECRET_SYNC_KEY>
X-GitHub-Event: push
```

#### Request Payload (GitHub Webhook Subset)

```json
{
  "ref": "refs/heads/main",
  "repository": {
    "name": "knowledge-base",
    "full_name": "organization/knowledge-base",
    "private": true
  },
  "pusher": {
    "name": "developer"
  }
}
```

#### Response (200 OK)

```json
{
  "status": "success",
  "message": "Content synchronization and database sync completed successfully",
  "processed_files": {
    "total": 12,
    "created": 1,
    "updated": 3,
    "deleted": 0,
    "ignored": 8
  },
  "timestamp": "2026-07-24T10:15:30Z"
}
```

---

## 2. Metadata Analysis and PostgreSQL Storage Engine

During the synchronization execution run, frontmatter data extracted from eligible Markdown files is analyzed, normalized, and stored into relational PostgreSQL tables.

### Internal Database Mapper (PostgreSQL Schema)

* **Tables**:
  * `cms.articles`: Stores primary metadata including title, slug, route, category, tags, dates, and processing flags.
  * `cms.article_sources`: Links Zotero citation keys with matching articles (Many-to-Many mapping table).
  * `cms.article_relations`: Stores self-referential article relations (`parent`, `children`, and extracted in-body links).

---

## 3. Sources and Zotero Conversion Engine

### `GET /api/v1/cms/sources`

Retrieves stored Zotero citation sources from the PostgreSQL database, formatting them as structured JSON objects for bibliographies and frontend applications.

#### Query Parameters

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `cite_key` | `string` | No | Filters results for a specific citation key (e.g., `Kappes2013`). |
| `article_id` | `integer` | No | Returns all citation sources associated with a specific article identifier. |

#### Response (200 OK)

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

---

## 4. Article Formatting and Link-Resolution Engine

The parsing engine processes internal Markdown links and converts them into a structured link-graph returned within JSON responses.

### Link Processing and Pre-Processing

* Translates relative repository links (e.g., `../security/grc/dsgvo.md`) to standard absolute frontend routing paths.
* Resolves double-bracket Wiki-links `[[Target]]` or `[[Target|Label]]` based on database slug or title matches.
* Generates styled fallback disabled anchor tags for unresolvable Wiki-links.
* Computes estimated reading metrics based on split word counts.

---

## 5. Frontend Consumer APIs

The frontend consumer tier queries the REST endpoints directly to construct navigation frameworks and visual graphs.

### `GET /api/v1/cms/articles`

Returns a list of published article metadata for overview indexing, search engine indexing, and sitemaps.

#### Query Parameters

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `published` | `boolean` | `true` | Restricts results to published articles only. |
| `category` | `string` | `null` | Filters results by a specific parent category. |
| `tag` | `string` | `null` | Filters results by an associated tag keyword. |
| `limit` | `integer` | `20` | Limits the maximum returned records. |

#### Response (200 OK)

```json
{
  "count": 1,
  "articles": [
    {
      "id": 3745094207,
      "title": "DSGVO: Data Protection Requirements in IT Infrastructure",
      "slug": "dsgvo",
      "route": "/network-security/kb/security/grc/dsgvo",
      "category": "security",
      "subcategory": "grc",
      "description": "Analysis of GDPR compliance requirements from an IT perspective.",
      "tags": ["DSGVO", "Privacy", "DataProtection", "TOM", "GDPR"],
      "date": "2025-10-06",
      "updated": "2026-04-20",
      "published": true,
      "has_math": false,
      "has_charts": false
    }
  ]
}
```

---

### `GET /api/v1/cms/articles/by-route`

Retrieves the complete article content, frontmatter metadata, resolved citations, and relational links matching a given routing path.

#### Query Parameters

* `route` (Required, `string`): The exact URL-encoded routing path in the frontend application.

#### Example Request

```http
GET /api/v1/cms/articles/by-route?route=%2Fnetwork-security%2Fkb%2Fsecurity%2Fgrc%2Fdsgvo
```

#### Response (200 OK)

```json
{
  "metadata": {
    "id": 3745094207,
    "title": "DSGVO: Data Protection Requirements in IT Infrastructure",
    "slug": "dsgvo",
    "route": "/network-security/kb/security/grc/dsgvo",
    "category": "security",
    "subcategory": "grc",
    "description": "Analysis of GDPR compliance from an IT perspective. Focus on Art. 32 (TOM), Privacy by Design, and data subject rights.",
    "tags": ["DSGVO", "Privacy", "DataProtection", "TOM", "GDPR", "Compliance"],
    "date": "2025-10-06",
    "updated": "2026-04-20",
    "published": true,
    "has_math": false,
    "has_charts": false
  },
  "content": {
    "raw_markdown": "# DSGVO: Data Protection Requirements...\n\nIn modern IT infrastructure...",
    "reading_time_minutes": 5
  },
  "sources": [
    {
      "cite_key": "DSGVO",
      "title": "Regulation (EU) 2016/679 of the European Parliament",
      "authors": [],
      "publisher": null,
      "year": 2016,
      "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679"
    },
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
    "related_articles": [
      {
        "id": 1092837482,
        "title": "BSI IT-Grundschutz vs. ISO 27001",
        "route": "/network-security/kb/security/grc/iso27001"
      }
    ]
  }
}
```

---

## Status Codes and Error Handling

| Status Code | Error Code | Description |
| :--- | :--- | :--- |
| `200 OK` | `-` | Request successfully processed. |
| `400 Bad Request` | `PATH_TRAVERSAL_DETECTED` | Path traversal sequence detected or path validation checks failed. |
| `401 Unauthorized` | `UNAUTHORIZED` | Request authentication failed due to missing or invalid webhook secrets. |
| `404 Not Found` | `ARTICLE_NOT_FOUND` | No database record exists for the requested route or article identifier. |
| `404 Not Found` | `ARTICLE_FILE_NOT_FOUND` | The processed Markdown source file is missing or inaccessible on disk. |
| `500 Internal Server Error`| `SYNC_FAILED` | Synchronization routine failed to run or database transaction aborted. |
