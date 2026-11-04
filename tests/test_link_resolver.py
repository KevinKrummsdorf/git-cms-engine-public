from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.article import ArticleModel
from services.link_resolver import resolve_markdown_links, parse_wiki_links


class _MockResult:
    def __init__(self, val):
        self.val = val

    def scalar_one_or_none(self):
        return self.val

    def scalars(self):
        return self

    def first(self):
        return self.val


class _MockSession:
    def __init__(self, routes_by_path: dict[str, str]):
        self.routes_by_path = routes_by_path
        self.executed_statements = []

    def execute(self, statement, *args, **kwargs):
        self.executed_statements.append(statement)
        compiled = statement.compile(compile_kwargs={"literal_binds": True})
        stmt_str = str(compiled)

        found_path = None
        for path in self.routes_by_path:
            if f"'{path}'" in stmt_str or f"file_path = {path}" in stmt_str:
                found_path = path
                break

        if found_path is None:
            params = statement.compile().params
            for p_val in params.values():
                if isinstance(p_val, (list, tuple, set)):
                    for sub_val in p_val:
                        if isinstance(sub_val, str) and sub_val in self.routes_by_path:
                            found_path = sub_val
                            break
                elif isinstance(p_val, str) and p_val in self.routes_by_path:
                    found_path = p_val
                    break

        val = self.routes_by_path.get(found_path) if found_path else None
        return _MockResult(val)


def test_resolve_markdown_links_basic() -> None:
    db = _MockSession({"security/dsgvo.md": "/legal/dsgvo"})
    content = "Schau dir [DSGVO Basics](dsgvo.md) an."
    current_file = "security/htb-craft.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == "Schau dir [DSGVO Basics](/articles/legal/dsgvo) an."


def test_parse_wiki_links_basic_and_pipe() -> None:
    # 1. With pipe
    res1 = parse_wiki_links("[[my-custom-slug|Mein angezeigter Titel]]")
    assert res1 == '<a href="/my-custom-slug">Mein angezeigter Titel</a>'

    # 2. Without pipe (fallback to slug)
    res2 = parse_wiki_links("[[my-custom-slug]]")
    assert res2 == '<a href="/my-custom-slug">my-custom-slug</a>'


def test_parse_wiki_links_whitespace_slashes_hyphens() -> None:
    # Whitespace handling
    res1 = parse_wiki_links("[[ slug | Text ]]")
    assert res1 == '<a href="/slug">Text</a>'

    # Slugs with hyphens and nested path slashes
    res2 = parse_wiki_links("[[ security/identity/oauth2-vs-oidc ]]")
    assert res2 == '<a href="/security/identity/oauth2-vs-oidc">security/identity/oauth2-vs-oidc</a>'


def test_parse_wiki_links_xss_escaping() -> None:
    # Display text containing HTML tags / special chars
    res = parse_wiki_links("[[ slug | <script>alert('xss')</script> & \"more\" ]]")
    assert res == '<a href="/slug">&lt;script&gt;alert(\'xss\')&lt;/script&gt; &amp; "more"</a>'


def test_parse_wiki_links_base_path_and_resolver() -> None:
    # Base path with trailing slash
    res1 = parse_wiki_links("[[my-custom-slug|Mein angezeigter Titel]]", base_path="/security/identity/")
    assert res1 == '<a href="/security/identity/my-custom-slug">Mein angezeigter Titel</a>'

    # Base path using camelCase alias
    res2 = parse_wiki_links("[[my-custom-slug|Mein angezeigter Titel]]", basePath="/docs/")
    assert res2 == '<a href="/docs/my-custom-slug">Mein angezeigter Titel</a>'

    # Custom resolver callback
    def custom_resolver(slug: str, display: str) -> str:
        return f"https://wiki.internal/kb/{slug}"

    res3 = parse_wiki_links("[[oauth2|OAuth 2.0 Spec]]", base_path=custom_resolver)
    assert res3 == '<a href="https://wiki.internal/kb/oauth2">OAuth 2.0 Spec</a>'


def test_parse_wiki_links_multiple_occurrences() -> None:
    text = "Hier ist [[intro|Einleitung]] und hier ein Link zu [[security/oauth2]] sowie [[ final-topic | Ende ]]."
    res = parse_wiki_links(text)
    expected = (
        'Hier ist <a href="/intro">Einleitung</a> und hier ein Link zu '
        '<a href="/security/oauth2">security/oauth2</a> sowie '
        '<a href="/final-topic">Ende</a>.'
    )
    assert res == expected


def test_parse_wiki_links_with_db_and_relations() -> None:
    db = _MockSession({"auth-vs-authz": "security/identity/auth-vs-authz"})
    res = parse_wiki_links("[[auth-vs-authz|Auth Guide]]", db=db)
    assert res == '<a href="/security/identity/auth-vs-authz">Auth Guide</a>'


def test_resolve_wiki_links_exists() -> None:
    db = _MockSession({"ecdh": "/crypto/ecdh"})
    content = "Lies [[ecdh]] oder [[ecdh|Custom Label]]."
    current_file = "security/test.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == "Lies [ecdh](/articles/crypto/ecdh) oder [Custom Label](/articles/crypto/ecdh)."


def test_resolve_wiki_links_not_exists() -> None:
    db = _MockSession({})
    content = "Lies [[notfound]] oder [[notfound|Custom Label]]."
    current_file = "security/test.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == (
        'Lies <a data-disabled="true" data-tooltip="Artikel demnächst verfügbar" class="disabled-wiki-link">notfound</a> '
        'oder <a data-disabled="true" data-tooltip="Artikel demnächst verfügbar" class="disabled-wiki-link">Custom Label</a>.'
    )


def test_resolve_wiki_links_nonexistent_target_not_linking_to_random_article() -> None:
    db = _MockSession({})
    target_slug = "os-kernel-entropy-pools-dev-random-urandom"
    label = "Entropie-Pools im Kernel-Design: Zustandsmischung, Schätzalgorithmen und der Mythos von /dev/random vs. /dev/urandom"
    content = f"Vorwort [[{target_slug}|{label}]] Nachwort"
    current_file = "security/test.md"

    result = resolve_markdown_links(content, current_file, db)
    expected = f'Vorwort <a data-disabled="true" data-tooltip="Artikel demnächst verfügbar" class="disabled-wiki-link">{label}</a> Nachwort'
    assert result == expected


def test_resolve_wiki_links_existent_target() -> None:
    target_slug = "os-kernel-entropy-pools-dev-random-urandom"
    db = _MockSession({target_slug: "/os/kernel/entropy-pools"})
    label = "Entropie-Pools im Kernel-Design: Zustandsmischung, Schätzalgorithmen und der Mythos von /dev/random vs. /dev/urandom"
    content = f"Vorwort [[{target_slug}|{label}]] Nachwort"
    current_file = "security/test.md"

    result = resolve_markdown_links(content, current_file, db)
    expected = f"Vorwort [{label}](/articles/os/kernel/entropy-pools) Nachwort"
    assert result == expected


def test_parse_wiki_links_with_db_not_found_returns_disabled_link() -> None:
    db = _MockSession({})
    res = parse_wiki_links("[[non-existent|Non Existent Guide]]", db=db)
    assert res == '<a data-disabled="true" data-tooltip="Artikel demnächst verfügbar" class="disabled-wiki-link">Non Existent Guide</a>'


def test_resolve_markdown_links_with_anchor_and_query() -> None:
    db = _MockSession({"security/dsgvo.md": "/legal/dsgvo"})
    content = "Schau dir [DSGVO Basics](dsgvo.md#section?p=1) an."
    current_file = "security/htb-craft.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == "Schau dir [DSGVO Basics](/articles/legal/dsgvo#section?p=1) an."


def test_resolve_markdown_links_ignore_external_and_images() -> None:
    db = _MockSession({"security/dsgvo.md": "/legal/dsgvo"})
    # Images with external links or already absolute shouldn't query DB
    content = (
        "Check [Google](https://google.com) or [Local Anchor](#local) "
        "or image ![Alt text](https://example.com/image.png)."
    )
    current_file = "security/htb-craft.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == content


def test_resolve_markdown_links_subdirectories() -> None:
    db = _MockSession({"security/gdpr/info.md": "/gdpr-info"})
    content = "Read [GDPR Info](gdpr/info.md)."
    current_file = "security/htb-craft.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == "Read [GDPR Info](/articles/gdpr-info)."


def test_resolve_image_links_relative() -> None:
    db = _MockSession({})  # Images shouldn't touch DB!
    content = "Check ![Proof of Concept](./assets/proof.png) here."
    current_file = "writeups/htb/craft.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == "Check ![Proof of Concept](/writeups/htb/assets/proof.png) here."
    # Ensure no SQL was executed since images aren't resolved through DB lookup
    assert len(db.executed_statements) == 0


def test_resolve_image_links_absolute() -> None:
    db = _MockSession({})
    content = "Check ![Birthday Attack](/resources/birthday-paradox/birthday_attack_cryptography.png) here."
    current_file = "kb/cryptography/birthday.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == "Check ![Birthday Attack](/data/resources/birthday-paradox/birthday_attack_cryptography.png) here."
    assert len(db.executed_statements) == 0


def test_resolve_image_links_external_untouched() -> None:
    db = _MockSession({})
    content = "Check ![Logo](https://example.com/images/logo.webp) here."
    current_file = "kb/cryptography/birthday.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == content
    assert len(db.executed_statements) == 0


def test_resolve_image_links_relative_with_parent() -> None:
    db = _MockSession({})
    content = "See ![Diagram](../../resources/diagram.svg) and some text."
    current_file = "writeups/htb/craft.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == "See ![Diagram](/data/resources/diagram.svg) and some text."
    assert len(db.executed_statements) == 0


def test_resolve_markdown_links_case_insensitive_and_strip_md() -> None:
    db = _MockSession({"security/dsgvo.MD": "/legal/dsgvo.MD"})
    content = "Schau dir [DSGVO Basics](dsgvo.MD) an."
    current_file = "security/htb-craft.md"

    result = resolve_markdown_links(content, current_file, db)
    assert result == "Schau dir [DSGVO Basics](/articles/legal/dsgvo) an."
