"""Content processing and database synchronization engine.

Recursively processes Markdown source files, performs frontmatter validations,
manages safe file copy procedures, and executes transactional PostgreSQL upserts
with stale cleanup operations.
"""

from __future__ import annotations

import os
import re
import datetime
import shutil
from pathlib import Path
from typing import Any
import frontmatter
from pydantic import ValidationError
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from config import settings
from models.article import ArticleModel, ArticleRelationModel, ArticleSourceModel, ZoteroSourceModel
from schemas.article import FrontmatterSchema
from services.link_resolver import resolve_markdown_links


class InvalidFrontmatterError(ValueError):
    """Raised when a Markdown file's frontmatter misses required fields or is malformed."""


def validate_frontmatter(meta: dict[str, Any], file_path: str) -> FrontmatterSchema:
    """Validiert das Frontmatter gegen ``FrontmatterSchema`` (Pflichtfelder werden erzwungen).

    Arbeitet auf einer Kopie, damit ``meta`` unverändert bleibt.
    """
    try:
        return FrontmatterSchema.model_validate(dict(meta or {}))
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise InvalidFrontmatterError(f"Invalid frontmatter in '{file_path}': {details}") from exc


def sync_resources_directory(raw_data_path: Path, processed_data_path: Path) -> None:
    """Kopiert statische Ressourcen ausschließlich aus raw_data/resources nach processed_data/resources."""
    src_dir = raw_data_path / "resources"
    dst_dir = processed_data_path / "resources"

    if not src_dir.exists():
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    for root, dirs, files in os.walk(src_dir):
        # Ordner wie .git, _templates etc. in resources ignorieren
        dirs[:] = [d for d in dirs if not d.startswith((".", "_")) and d != "private"]

        rel_path = os.path.relpath(root, src_dir)
        target_dir = dst_dir if rel_path == "." else dst_dir / rel_path
        target_dir.mkdir(parents=True, exist_ok=True)

        for file in files:
            if file.startswith((".", "_")):
                continue

            src_file = Path(root) / file
            dst_file = target_dir / file

            if not dst_file.exists() or src_file.stat().st_mtime > dst_file.stat().st_mtime:
                try:
                    shutil.copy2(src_file, dst_file)
                except Exception as e:
                    print(f"[RESOURCES ERROR] Failed to copy asset {src_file} to {dst_file}: {e}")


def normalize_date(val: Any, default_to_today: bool = True) -> datetime.date | None:
    if val is None or val == "":
        return datetime.date.today() if default_to_today else None
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return datetime.date.today() if default_to_today else None
        try:
            return datetime.date.fromisoformat(val_str)
        except ValueError:
            pass
        for fmt in ("%d.%m.%Y", "%d.%m.%y"):
            try:
                return datetime.datetime.strptime(val_str, fmt).date()
            except ValueError:
                pass
    return datetime.date.today() if default_to_today else None


def slugify_text(val: str | None) -> str | None:
    if not val:
        return None
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", val.strip().lower()).strip("-")
    return cleaned or None


def compare_and_update_article(
    existing_article: ArticleModel,
    meta_title: str,
    meta_slug: str,
    meta_route: str,
    meta_category: str,
    meta_subcategory: str | None,
    meta_subsubcategory: str | None,
    meta_description: str | None,
    meta_tags: list[str],
    meta_date: Any,
    meta_updated: Any,
    meta_status: str,
    meta_published: bool,
    meta_math: bool,
    meta_charts: bool,
    meta_relevance: str | None,
    file_path: str,
    is_new: bool,
    meta_parent: list[str] | None = None,
    meta_children: list[str] | None = None,
    meta_related: list[str] | None = None,
    meta_series_id: str | None = None,
    meta_series_order: int = 0,
) -> bool:
    norm_date = normalize_date(meta_date)
    norm_updated = normalize_date(meta_updated, default_to_today=False)
    norm_tags = list(meta_tags) if meta_tags is not None else []

    norm_category = str(meta_category).strip() if meta_category else "General"
    norm_subcategory = str(meta_subcategory).strip() if meta_subcategory else None
    norm_subsubcategory = str(meta_subsubcategory).strip() if meta_subsubcategory else None

    norm_description = meta_description if meta_description is not None else None
    norm_relevance = meta_relevance if meta_relevance is not None else None
    norm_parent = list(meta_parent) if meta_parent is not None else []
    norm_children = list(meta_children) if meta_children is not None else []
    norm_related = list(meta_related) if meta_related is not None else []

    norm_series_id = str(meta_series_id).strip() if meta_series_id else None
    norm_series_order = int(meta_series_order) if meta_series_order is not None else 0

    changed = False

    if existing_article.title != meta_title:
        existing_article.title = meta_title
        changed = True

    if existing_article.slug != meta_slug:
        existing_article.slug = meta_slug
        changed = True

    if existing_article.route != meta_route:
        existing_article.route = meta_route
        changed = True

    current_category = str(existing_article.category).strip() if existing_article.category else None
    current_subcategory = str(existing_article.subcategory).strip() if existing_article.subcategory else None
    current_subsubcategory = str(existing_article.subsubcategory).strip() if existing_article.subsubcategory else None

    if current_category != norm_category:
        existing_article.category = norm_category
        changed = True

    if current_subcategory != norm_subcategory:
        existing_article.subcategory = norm_subcategory
        changed = True

    if current_subsubcategory != norm_subsubcategory:
        existing_article.subsubcategory = norm_subsubcategory
        changed = True

    if (existing_article.description or None) != norm_description:
        existing_article.description = norm_description
        changed = True

    tags_a = list(existing_article.tags) if existing_article.tags is not None else []
    if tags_a != norm_tags:
        existing_article.tags = norm_tags
        changed = True

    if existing_article.date != norm_date:
        existing_article.date = norm_date
        changed = True

    if existing_article.updated != norm_updated:
        existing_article.updated = norm_updated
        changed = True

    if existing_article.status != meta_status:
        existing_article.status = meta_status
        changed = True

    if existing_article.published != meta_published:
        existing_article.published = meta_published
        changed = True

    if existing_article.math != meta_math:
        existing_article.math = meta_math
        changed = True

    if existing_article.charts != meta_charts:
        existing_article.charts = meta_charts
        changed = True

    if (existing_article.relevance or None) != norm_relevance:
        existing_article.relevance = norm_relevance
        changed = True

    if list(existing_article.parent or []) != norm_parent:
        existing_article.parent = norm_parent
        changed = True

    if list(existing_article.children or []) != norm_children:
        existing_article.children = norm_children
        changed = True

    if list(existing_article.related or []) != norm_related:
        existing_article.related = norm_related
        changed = True

    curr_series_id = str(existing_article.series_id).strip() if existing_article.series_id else None
    if curr_series_id != norm_series_id:
        existing_article.series_id = norm_series_id
        changed = True

    curr_series_order = existing_article.series_order if existing_article.series_order is not None else 0
    if curr_series_order != norm_series_order:
        existing_article.series_order = norm_series_order
        changed = True

    if existing_article.file_path != file_path:
        existing_article.file_path = file_path
        changed = True

    if changed and not is_new and norm_updated is None:
        existing_article.updated = datetime.date.today()

    return changed


def handle_route_and_slug_conflicts(
    db: Session,
    current_file_path: str,
    meta_route: str,
    meta_slug: str,
    active_file_paths: set[str],
) -> None:
    colliding_route_art = db.execute(
        select(ArticleModel).where(ArticleModel.route == meta_route)
    ).scalars().first()

    if colliding_route_art and colliding_route_art.file_path != current_file_path:
        if colliding_route_art.file_path not in active_file_paths:
            db.delete(colliding_route_art)
            db.flush()
        else:
            raise ValueError(f"Route conflict / Routen-Konflikt: The route '/{meta_route}' is already occupied.")

    colliding_slug_art = db.execute(
        select(ArticleModel).where(ArticleModel.slug == meta_slug)
    ).scalars().first()

    if colliding_slug_art and colliding_slug_art.file_path != current_file_path:
        if colliding_slug_art.file_path not in active_file_paths:
            db.delete(colliding_slug_art)
            db.flush()
        else:
            raise ValueError(f"Slug conflict: The slug '{meta_slug}' is already occupied.")


def upsert_single_article(
    db: Session,
    file_path: str,
    meta: dict[str, Any],
    active_file_paths: set[str],
) -> ArticleModel:
    # Pflichtfelder erzwingen – wirft InvalidFrontmatterError, keine stillen Fallbacks mehr.
    fm = validate_frontmatter(meta, file_path)

    slug_val = fm.slug
    clean_route = fm.route.strip("/")
    if not clean_route:
        raise InvalidFrontmatterError(f"Invalid frontmatter in '{file_path}': route: must not be empty")

    handle_route_and_slug_conflicts(db, file_path, clean_route, slug_val, active_file_paths)

    existing_article = db.execute(
        select(ArticleModel).where(ArticleModel.file_path == file_path)
    ).scalars().first()

    if not existing_article:
        existing_article = db.execute(
            select(ArticleModel).where(ArticleModel.route == clean_route)
        ).scalars().first()

    is_new = False
    if not existing_article:
        is_new = True
        existing_article = ArticleModel(
            id=fm.id,
            file_path=file_path,
            route=clean_route,
            slug=slug_val,
        )
        db.add(existing_article)

    meta_series_id = None
    meta_series_order = 0
    if fm.series is not None:
        meta_series_id = fm.series.id or slugify_text(fm.subsubcategory)
        meta_series_order = fm.series.order

    def _normalize_refs(values: list[str]) -> list[str]:
        return [re.sub(r"\s+", "-", v.strip().lower()) for v in values if v.strip()]

    changed = compare_and_update_article(
        existing_article=existing_article,
        meta_title=fm.title,
        meta_slug=slug_val,
        meta_route=clean_route,
        meta_category=fm.category,
        meta_subcategory=fm.subcategory,
        meta_subsubcategory=fm.subsubcategory,
        meta_description=fm.description,
        meta_tags=fm.tags,
        meta_date=fm.date,
        meta_updated=fm.updated,
        meta_status=fm.status,
        meta_published=fm.published,
        meta_math=fm.math,
        meta_charts=fm.charts,
        meta_relevance=fm.relevance,
        meta_parent=_normalize_refs(fm.parent),
        meta_children=_normalize_refs(fm.children),
        meta_related=_normalize_refs(fm.related),
        file_path=file_path,
        is_new=is_new,
        meta_series_id=meta_series_id,
        meta_series_order=meta_series_order,
    )

    if changed or is_new:
        db.add(existing_article)

    action = "created" if is_new else ("updated" if changed else "no_change")
    existing_article._sync_action = action

    db.flush()
    return existing_article


def rebuild_all_relations_and_sources(db: Session) -> None:
    link_pattern = re.compile(r"\[(?P<title>[^\]]*)\]\((?P<target>[^)\s]+\.md(?:#[^)\s]*)?)\)")
    external_scheme_pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

    def clean_path_key(p: str) -> str:
        p_str = str(p).replace("\\", "/").lower()
        if p_str.startswith("content/"):
            p_str = p_str[len("content/"):]
        return p_str.strip("/")

    def clean_route_key(r: str) -> str:
        return str(r).strip("/").lower()

    def normalize_key(s: str) -> str:
        return "".join(c.lower() for c in s if c.isalnum())

    def _normalize_relative_path(current_file_path: str, relative_target: str) -> str:
        try:
            target_path_str = relative_target.split("#")[0].split("?")[0]
            if not target_path_str.endswith(".md"):
                return ""
            current_dir = Path(current_file_path).parent
            norm = os.path.normpath(os.path.join(current_dir, target_path_str))
            return norm.replace("\\", "/")
        except Exception:
            return ""

    def _find_article_file(file_path_str: str) -> Path | None:
        roots = [
            settings.cms_processed_data_path,
            settings.cms_raw_data_path,
            Path("/app/content"),
            Path("./content"),
            Path(os.getcwd()) / "content",
        ]
        for r in roots:
            if not r:
                continue
            p1 = r / file_path_str
            if p1.exists() and p1.is_file():
                return p1
            if file_path_str.startswith("content/"):
                p2 = r / file_path_str[len("content/"):]
                if p2.exists() and p2.is_file():
                    return p2
            else:
                p3 = r / "content" / file_path_str
                if p3.exists() and p3.is_file():
                    return p3
        return None

    articles = db.execute(select(ArticleModel)).scalars().all()
    if not articles:
        return

    valid_cite_keys_rows = db.execute(select(ZoteroSourceModel.cite_key)).scalars().all()
    exact_cite_map = {k.lower().strip(): k for k in valid_cite_keys_rows}
    norm_cite_map = {normalize_key(k): k for k in valid_cite_keys_rows}

    slug_map = {art.slug: art for art in articles if art.slug}
    route_map = {clean_route_key(art.route): art for art in articles if art.route}
    path_to_id = {clean_path_key(art.file_path): art.id for art in articles if art.file_path}

    def resolve_target(target_str: str) -> ArticleModel | None:
        target_str_clean = target_str.strip()
        if not target_str_clean:
            return None
        target = slug_map.get(target_str_clean)
        if target:
            return target
        return route_map.get(clean_route_key(target_str_clean))

    db.execute(delete(ArticleRelationModel))
    db.execute(delete(ArticleSourceModel))
    db.flush()

    inserted_relations = []
    inserted_relations_set = set()
    inserted_sources = set()

    for art in articles:
        f_path = _find_article_file(art.file_path)
        if not f_path:
            continue

        try:
            with open(f_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
                meta = post.metadata or {}
                content = post.content or ""
        except Exception:
            continue

        parents_list = meta.get("parent") or []
        if isinstance(parents_list, str):
            parents_list = [p.strip() for p in parents_list.split(",") if p.strip()]
        elif not isinstance(parents_list, list):
            parents_list = []

        children_list = meta.get("children") or meta.get("child") or []
        if isinstance(children_list, str):
            children_list = [c.strip() for c in children_list.split(",") if c.strip()]
        elif not isinstance(children_list, list):
            children_list = []

        related_list = meta.get("related") or []
        if isinstance(related_list, str):
            related_list = [r.strip() for r in related_list.split(",") if r.strip()]
        elif not isinstance(related_list, list):
            related_list = []

        raw_source_keys = []
        r_sources = meta.get("related_sources") or []
        if isinstance(r_sources, str):
            r_sources = [s.strip() for s in r_sources.split(",") if s.strip()]
        elif isinstance(r_sources, list):
            r_sources = [str(s).strip() for s in r_sources]
        else:
            r_sources = []
        raw_source_keys.extend(r_sources)

        rel_str = meta.get("relevance")
        if isinstance(rel_str, str):
            rel_sources = [s.strip() for s in rel_str.split(",") if s.strip()]
            raw_source_keys.extend(rel_sources)

        internal_links = []
        for match in link_pattern.finditer(content):
            target = match.group("target").strip()
            if external_scheme_pattern.match(target):
                continue
            internal_links.append(target)

        for p_str in parents_list:
            target_article = resolve_target(p_str)
            target_id = target_article.id if target_article else None
            target_route = target_article.route if target_article else str(p_str).strip()
            if target_id == art.id:
                continue
            key = (art.id, target_id, target_route, 'parent')
            if key not in inserted_relations_set:
                inserted_relations_set.add(key)
                inserted_relations.append(ArticleRelationModel(
                    article_id=art.id,
                    target_article_id=target_id,
                    target_route=target_route,
                    relation_type='parent'
                ))

        for c_str in children_list:
            target_article = resolve_target(c_str)
            target_id = target_article.id if target_article else None
            target_route = target_article.route if target_article else str(c_str).strip()
            if target_id == art.id:
                continue
            key = (art.id, target_id, target_route, 'children')
            if key not in inserted_relations_set:
                inserted_relations_set.add(key)
                inserted_relations.append(ArticleRelationModel(
                    article_id=art.id,
                    target_article_id=target_id,
                    target_route=target_route,
                    relation_type='children'
                ))

        for r_str in related_list:
            target_article = resolve_target(r_str)
            target_id = target_article.id if target_article else None
            target_route = target_article.route if target_article else str(r_str).strip()
            if target_id == art.id:
                continue
            key = (art.id, target_id, target_route, 'related')
            if key not in inserted_relations_set:
                inserted_relations_set.add(key)
                inserted_relations.append(ArticleRelationModel(
                    article_id=art.id,
                    target_article_id=target_id,
                    target_route=target_route,
                    relation_type='related'
                ))

        for link in internal_links:
            norm_path = _normalize_relative_path(art.file_path, link)
            tid = path_to_id.get(clean_path_key(norm_path))
            if tid and tid != art.id:
                target_article = route_map.get(clean_route_key(link)) or next((a for a in articles if a.id == tid), None)
                target_route = target_article.route if target_article else link
                key = (art.id, tid, target_route, 'internal')
                if key not in inserted_relations_set:
                    inserted_relations_set.add(key)
                    inserted_relations.append(ArticleRelationModel(
                        article_id=art.id,
                        target_article_id=tid,
                        target_route=target_route,
                        relation_type='internal'
                    ))

        for s_key in raw_source_keys:
            s_key_clean = s_key.strip()
            s_key_lower = s_key_clean.lower()
            s_key_norm = normalize_key(s_key_clean)

            matched_key = None
            if s_key_lower in exact_cite_map:
                matched_key = exact_cite_map[s_key_lower]
            elif s_key_norm in norm_cite_map:
                matched_key = norm_cite_map[s_key_norm]

            if matched_key:
                inserted_sources.add((art.id, matched_key))

    for relation in inserted_relations:
        db.add(relation)

    for art_id, s_key in sorted(inserted_sources):
        db.add(ArticleSourceModel(article_id=art_id, source_key=s_key))

    db.flush()


def process_all_markdown_files(db: Session) -> dict[str, Any]:
    stats = {
        "total": 0,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "ignored": 0,
        "invalid": 0,
    }

    try:
        raw_data_path = settings.cms_raw_data_path
        processed_data_path = settings.cms_processed_data_path

        if not raw_data_path.exists():
            print(f"[PROCESSOR WARNING] Raw data path '{raw_data_path}' does not exist.")
            return stats

        processed_data_path.mkdir(parents=True, exist_ok=True)
        sync_resources_directory(raw_data_path, processed_data_path)

        UNPUBLISHED_STATUSES = {"draft", "bilder", "review", "archived", "private"}

        active_files: list[tuple[str, Path, Path, dict[str, Any], str]] = []
        active_file_paths: set[str] = set()

        for root, dirs, files in os.walk(raw_data_path):
            # STRIKTE ISOLATION: Ignoriere private, _templates, versteckte Ordner
            dirs[:] = [d for d in dirs if not d.startswith((".", "_")) and d != "private"]

            for file in files:
                if not file.endswith(".md"):
                    continue

                file_path = Path(root) / file
                relative_file_path = file_path.relative_to(raw_data_path)
                rel_parts = relative_file_path.parts

                # 1. Ignoriere Dateien, die nicht im Ordner 'content/' liegen (z.B. Root-Dateien, private, _templates)
                if not rel_parts or rel_parts[0] != "content":
                    stats["ignored"] += 1
                    continue

                # 2. Ignoriere versteckte Dateien oder Template-Dateien
                if any(p.startswith((".", "_")) for p in rel_parts):
                    stats["ignored"] += 1
                    continue

                stats["total"] += 1
                rel_file_path_str = str(relative_file_path).replace("\\", "/")

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        post = frontmatter.load(f)
                except Exception as e:
                    print(f"[PROCESSOR ERROR] Failed to load frontmatter in {file_path}: {e}")
                    stats["ignored"] += 1
                    continue

                meta = post.metadata or {}
                raw_content = post.content or ""

                status_raw = meta.get("status")
                status_str = str(status_raw).strip().lower() if status_raw else "published"

                published_field = meta.get("published")
                if published_field is not None:
                    is_published = bool(published_field)
                else:
                    is_published = (status_str not in UNPUBLISHED_STATUSES)

                if not is_published:
                    print(f"[PROCESSOR SKIP] Skipping unpublished article {relative_file_path} (status: '{status_raw}')")
                    stats["ignored"] += 1
                    continue

                if settings.cms_allowed_folders:
                    clean_rel_path = rel_file_path_str
                    if clean_rel_path.startswith("content/"):
                        clean_rel_path = clean_rel_path[len("content/"):]

                    allowed = False
                    for folder in settings.cms_allowed_folders:
                        folder_stripped = folder.strip("/")
                        if clean_rel_path == folder_stripped or clean_rel_path.startswith(folder_stripped + "/"):
                            allowed = True
                            break
                        if rel_file_path_str == folder_stripped or rel_file_path_str.startswith(folder_stripped + "/"):
                            allowed = True
                            break

                    if not allowed:
                        print(f"[PROCESSOR FILTER] Skipping {relative_file_path} (Not in allowed list: {settings.cms_allowed_folders})")
                        stats["ignored"] += 1
                        continue

                # Pflichtfelder erzwingen: ungültige Artikel werden nicht synchronisiert
                # (und ggf. vorhandene DB-Einträge beim Stale-Cleanup entfernt).
                try:
                    validate_frontmatter(meta, rel_file_path_str)
                except InvalidFrontmatterError as e:
                    print(f"[PROCESSOR INVALID] Skipping {relative_file_path}: {e}")
                    stats["invalid"] += 1
                    continue

                active_files.append((rel_file_path_str, file_path, relative_file_path, meta, raw_content))
                active_file_paths.add(rel_file_path_str)

        # Phase 1: Upsert all active articles to database
        for rel_file_path_str, file_path, relative_file_path, meta, raw_content in active_files:
            try:
                with db.begin_nested():
                    art = upsert_single_article(db, rel_file_path_str, meta, active_file_paths)
                    action = getattr(art, "_sync_action", "no_change")
                    if action == "created":
                        stats["created"] += 1
                    elif action == "updated":
                        stats["updated"] += 1
            except Exception as e:
                print(f"[PROCESSOR ERROR] DB Upsert failed for {relative_file_path}: {e}")
                continue

        # Phase 2: Cleanup stale articles from database and disk
        try:
            all_db_articles = db.execute(select(ArticleModel)).scalars().all()
            for art in all_db_articles:
                if art.file_path not in active_file_paths:
                    print(f"[CLEANUP] Removing stale database metadata for article: {art.file_path}")

                    processed_file_on_disk = processed_data_path / art.file_path
                    if processed_file_on_disk.exists() and processed_file_on_disk.is_file():
                        try:
                            processed_file_on_disk.unlink()
                            print(f"[CLEANUP DISK OK] Deleted processed file from disk: {processed_file_on_disk}")

                            parent_dir = processed_file_on_disk.parent
                            while parent_dir != processed_data_path and parent_dir.exists():
                                if not any(parent_dir.iterdir()):
                                    parent_dir.rmdir()
                                    parent_dir = parent_dir.parent
                                else:
                                    break
                        except Exception as e:
                            print(f"[CLEANUP DISK ERROR] Failed to delete file {processed_file_on_disk}: {e}")

                    db.delete(art)
                    stats["deleted"] += 1

            if stats["deleted"] > 0:
                db.flush()
                print(f"[CLEANUP OK] Successfully deleted {stats['deleted']} stale articles.")
        except Exception as e:
            print(f"[CLEANUP ERROR] Stale cleanup execution failed: {e}")

        # Phase 3: Resolve markdown links against current CMS database state and write to disk
        for rel_file_path_str, file_path, relative_file_path, meta, raw_content in active_files:
            try:
                resolved_content = resolve_markdown_links(raw_content, rel_file_path_str, db)
            except Exception as e:
                print(f"[PROCESSOR WARNING] Failed to resolve links for {relative_file_path}: {e}")
                resolved_content = raw_content

            target_file_path = processed_data_path / relative_file_path
            target_file_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                processed_post = frontmatter.Post(resolved_content, **meta)
                with open(target_file_path, "w", encoding="utf-8") as f:
                    f.write(frontmatter.dumps(processed_post))
            except Exception as e:
                print(f"[PROCESSOR ERROR] Failed to write processed file {target_file_path}: {e}")
                continue

            raw_route = meta.get("route") or str(relative_file_path.with_suffix("")).replace("\\", "/")
            clean_route = str(raw_route).strip("/")
            print(f"[PROCESSOR OK] Successfully compiled: {relative_file_path} -> Route: /{clean_route}")

        try:
            rebuild_all_relations_and_sources(db)
        except Exception as e:
            print(f"[PROCESSOR ERROR] Failed to rebuild relations and sources: {e}")

        db.commit()
        return stats
    except Exception as e:
        db.rollback()
        print(f"[PROCESSOR FATAL ERROR] Processing failed, rolled back changes: {e}")
        raise
