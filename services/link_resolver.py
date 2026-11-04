"""Markdown and Wiki-link routing resolution service.

This module provides functions to parse, transform, and resolve internal links
and image asset paths embedded in Markdown content. It maps relative file paths,
statically mounted resources, and double-bracket Wiki-links into standard
routing paths resolved against database records.
"""

from __future__ import annotations

import html
import re
import os
from typing import Callable, Union, Optional
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from models.article import ArticleModel

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
_WIKI_LINK_PARSER_REGEX = re.compile(r"\[\[\s*([^\]|]+?)\s*(?:\|\s*([^\]]*?)\s*)?\]\]")


def parse_wiki_links(
    text: str,
    base_path: Union[str, Callable[[str, str], str]] = "/",
    *,
    basePath: Optional[Union[str, Callable[[str, str], str]]] = None,
    db: Optional[Session] = None,
) -> str:
    """Parses Obsidian-style Wiki-links into standard HTML anchor tags (`<a>`).

    Parses double-bracket Wiki-links in format `[[slug|display_text]]` or `[[slug]]`,
    automatically trimming internal whitespace, escaping inner HTML text against XSS,
    and constructing standard href attributes resolved against database routes or base paths.

    Args:
        text: The input text containing Wiki-links.
        base_path: Standard base URL path string (default "/") or a resolver callback
            `Callable[[slug, display_text], str]` generating the target URL.
        basePath: Optional alias for `base_path` to support camelCase parameter calls.
        db: Optional SQLAlchemy database session for dynamic route lookups.

    Returns:
        The string with all Wiki-link occurrences replaced with HTML `<a>` tags.
    """
    if not text:
        return ""

    active_resolver = basePath if basePath is not None else base_path

    def _replace_match(match: re.Match[str]) -> str:
        raw_slug = match.group(1) or ""
        raw_display = match.group(2)

        slug = raw_slug.strip()
        if raw_display is not None and raw_display.strip() != "":
            display_text = raw_display.strip()
        else:
            display_text = slug

        target_route: Optional[str] = None

        if db is not None:
            base_target = slug
            anchor = ""
            if "#" in slug:
                base_target, anchor = slug.split("#", 1)
                base_target = base_target.strip()
                anchor = anchor.strip()

            target_clean = base_target.strip("/")
            if target_clean.lower().endswith(".md"):
                target_clean = target_clean[:-3]

            target_filename = os.path.basename(base_target)
            if target_filename.lower().endswith(".md"):
                target_filename = target_filename[:-3]

            stmt = select(ArticleModel.route).where(
                or_(
                    func.lower(ArticleModel.slug) == func.lower(base_target),
                    func.lower(ArticleModel.title) == func.lower(base_target),
                    func.lower(ArticleModel.slug) == func.lower(target_clean),
                    func.lower(ArticleModel.title) == func.lower(target_clean),
                    func.lower(ArticleModel.route) == func.lower(target_clean),
                    func.lower(ArticleModel.slug) == func.lower(target_filename),
                    func.lower(ArticleModel.file_path) == func.lower(target_clean + ".md"),
                    ArticleModel.file_path.ilike(f"%/{target_clean}.md"),
                    ArticleModel.file_path.ilike(f"%/{target_filename}.md"),
                )
            )
            route_result = db.execute(stmt).scalars().first()
            if route_result:
                clean_r = route_result.strip("/")
                if clean_r.lower().endswith(".md"):
                    clean_r = clean_r[:-3]
                target_route = f"/{clean_r}{('#' + anchor if anchor else '')}"

        if target_route is not None:
            href = target_route
        elif db is not None:
            escaped_display = html.escape(display_text, quote=False)
            return f'<a data-disabled="true" data-tooltip="Artikel demnächst verfügbar" class="disabled-wiki-link">{escaped_display}</a>'
        elif callable(active_resolver):
            href = active_resolver(slug, display_text)
        else:
            bp = str(active_resolver).strip()
            if bp in ("", "/"):
                href = f"/{slug.lstrip('/')}"
            else:
                bp_clean = bp.rstrip("/")
                slug_clean = slug.lstrip("/")
                href = f"{bp_clean}/{slug_clean}"

        escaped_href = html.escape(href, quote=True)
        escaped_display = html.escape(display_text, quote=False)

        return f'<a href="{escaped_href}">{escaped_display}</a>'

    return _WIKI_LINK_PARSER_REGEX.sub(_replace_match, text)


def resolve_markdown_links(markdown_content: str, current_file_path: str, db: Session) -> str:
    """Parses and resolves embedded image assets, relative standard links, and Wiki-links."""
    if not markdown_content:
        return ""

    # Pass 1: Resolve image links using pattern `![Alt-Text](image_path)`
    image_pattern = re.compile(r'\!\[([^\]]*)\]\(([^)]+)\)')

    def replace_image(match):
        alt_text = match.group(1)
        link_path_raw = match.group(2).strip()

        # Skip remote images
        if link_path_raw.startswith(("http://", "https://")):
            return match.group(0)

        path_without_anchor, anchor = (link_path_raw.split('#', 1) + ['', ''])[0:2]
        path_without_query, query = (path_without_anchor.split('?', 1) + ['', ''])[0:2]

        ext = os.path.splitext(path_without_query)[1].lower()
        if ext in _IMAGE_EXTENSIONS:
            if path_without_query.startswith("/"):
                if path_without_query.startswith("/resources/"):
                    path_without_query = "/data" + path_without_query
                    resolved_link = f"{path_without_query}{('#' + anchor if anchor else '')}{('?' + query if query else '')}"
                    return f"![{alt_text}]({resolved_link})"
                return match.group(0)

            current_file_dir = os.path.dirname(current_file_path)
            combined_path = os.path.normpath(os.path.join(current_file_dir, path_without_query))
            normalized_path = combined_path.replace("\\", "/")
            if not normalized_path.startswith("/"):
                normalized_path = "/" + normalized_path

            if normalized_path.startswith("/resources/"):
                normalized_path = "/data" + normalized_path

            resolved_link = f"{normalized_path}{('#' + anchor if anchor else '')}{('?' + query if query else '')}"
            return f"![{alt_text}]({resolved_link})"

        return match.group(0)

    content_resolved_images = image_pattern.sub(replace_image, markdown_content)

    # Pass 2: Resolve standard links
    link_pattern = re.compile(r'(?<!\!)\[([^\]]+)\]\((?!https?://|#)([^)]+)\)')

    def replace_link(match):
        link_text = match.group(1)
        link_path_raw = match.group(2).strip()

        path_without_anchor, anchor = (link_path_raw.split('#', 1) + ['', ''])[0:2]
        path_without_query, query = (path_without_anchor.split('?', 1) + ['', ''])[0:2]

        current_file_dir = os.path.dirname(current_file_path)
        combined_path = os.path.normpath(os.path.join(current_file_dir, path_without_query))
        normalized_path = combined_path.replace("\\", "/").lstrip("/")

        target_filename = os.path.basename(path_without_query)
        filename_without_ext = os.path.splitext(target_filename)[0]

        stmt = select(ArticleModel.route).where(
            or_(
                func.lower(ArticleModel.file_path) == func.lower(normalized_path),
                ArticleModel.file_path.ilike(f"%{normalized_path}"),
                ArticleModel.file_path.ilike(f"%/{target_filename}"),
                func.lower(ArticleModel.slug) == func.lower(filename_without_ext)
            )
        )
        route_result = db.execute(stmt).scalars().first()

        if route_result:
            clean_route = route_result.strip("/")
            if clean_route.lower().endswith(".md"):
                clean_route = clean_route[:-3]
            # Absoluter Link von Root aus:
            resolved_url = f"/articles/{clean_route}{('#' + anchor if anchor else '')}{('?' + query if query else '')}"
            return f"[{link_text}]({resolved_url})"

        return match.group(0)

    content_resolved_standard = link_pattern.sub(replace_link, content_resolved_images)

    # Pass 3: Resolve Wiki-links
    wiki_pattern = re.compile(r"\[\[(?P<target>[^\]|]+)(?:\|(?P<label>[^\]]+))?\]\]")

    def replace_wiki(match):
        target = match.group("target").strip()
        label = match.group("label")
        if label is not None:
            label = label.strip()
        else:
            label = target

        base_target = target
        anchor = ""
        if "#" in target:
            base_target, anchor = target.split("#", 1)
            base_target = base_target.strip()
            anchor = anchor.strip()

        target_clean = base_target.strip("/")
        if target_clean.lower().endswith(".md"):
            target_clean = target_clean[:-3]

        target_filename = os.path.basename(base_target)
        if target_filename.lower().endswith(".md"):
            target_filename = target_filename[:-3]

        stmt = select(ArticleModel.route).where(
            or_(
                func.lower(ArticleModel.slug) == func.lower(base_target),
                func.lower(ArticleModel.title) == func.lower(base_target),
                func.lower(ArticleModel.slug) == func.lower(target_clean),
                func.lower(ArticleModel.title) == func.lower(target_clean),
                func.lower(ArticleModel.route) == func.lower(target_clean),
                func.lower(ArticleModel.slug) == func.lower(target_filename),
                func.lower(ArticleModel.file_path) == func.lower(target_clean + ".md"),
                ArticleModel.file_path.ilike(f"%/{target_clean}.md"),
                ArticleModel.file_path.ilike(f"%/{target_filename}.md"),
            )
        )
        route_result = db.execute(stmt).scalars().first()

        if route_result:
            clean_route = route_result.strip("/")
            if clean_route.lower().endswith(".md"):
                clean_route = clean_route[:-3]
            # Absoluter Link von Root aus:
            resolved_url = f"/articles/{clean_route}{('#' + anchor if anchor else '')}"
            return f"[{label}]({resolved_url})"

        return f'<a data-disabled="true" data-tooltip="Artikel demnächst verfügbar" class="disabled-wiki-link">{label}</a>'

    return wiki_pattern.sub(replace_wiki, content_resolved_standard)
