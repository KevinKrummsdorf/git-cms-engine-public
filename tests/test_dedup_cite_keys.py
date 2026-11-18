from services.processor import deduplicate_cite_keys


def test_deduplicate_cite_keys_case_insensitive_order_preserving(capsys):
    result = deduplicate_cite_keys(["RFC8446", "rfc8446", "NIST800-56Ar3", "RFC8446"], file_path="a.md")
    assert result == ["RFC8446", "NIST800-56Ar3"]
    assert "[PROCESSOR INFO]" in capsys.readouterr().out


def test_deduplicate_cite_keys_edge_cases():
    assert deduplicate_cite_keys(None) == []
    assert deduplicate_cite_keys("  RFC1  ") == ["RFC1"]
    assert deduplicate_cite_keys(["", "  ", None, "x"]) == ["x"]


def test_deduplicate_cite_keys_skips_dois(capsys):
    result = deduplicate_cite_keys(
        [
            "10.1093/oso/9780198826491.001.0001",
            "https://doi.org/10.6028/NIST.SP.800-52r2",
            "RFC8446",
            "rfc8446",
        ]
    )
    assert result == ["RFC8446"]
    out = capsys.readouterr().out
    assert "[PROCESSOR INFO] DOI '10.1093/oso/9780198826491.001.0001' übersprungen" in out


def test_is_doi():
    from services.processor import is_doi

    assert is_doi("10.1093/oso/9780198826491.001.0001")
    assert is_doi("doi:10.1093/oso/9780198826491.001.0001")
    assert is_doi("https://doi.org/10.6028/NIST.SP.800-52r2")
    assert not is_doi("RFC8446")


def test_deduplicate_cite_keys_collects_warnings(capsys):
    w: list[str] = []
    result = deduplicate_cite_keys(
        ["10.1093/oso/9780198826491.001.0001", "RFC8446", "rfc8446"], file_path="test.md", warnings=w
    )
    assert result == ["RFC8446"]
    expected = [
        "[PROCESSOR INFO] DOI '10.1093/oso/9780198826491.001.0001' übersprungen in test.md",
        "[PROCESSOR INFO] Doppelter cite_key 'rfc8446' ignoriert in test.md",
    ]
    assert w == expected
    out = capsys.readouterr().out
    for msg in expected:
        assert msg in out


def test_deduplicate_cite_keys_log_warnings_false(capsys):
    result = deduplicate_cite_keys(
        ["10.1093/oso/9780198826491.001.0001", "RFC8446", "rfc8446"], log_warnings=False
    )
    assert result == ["RFC8446"]
    assert capsys.readouterr().out == ""
