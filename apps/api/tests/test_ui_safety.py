from pathlib import Path

PAGE = (Path(__file__).parents[1] / "src/stratum_api/static/index.html").read_text()


def test_the_page_escapes_what_it_puts_into_html() -> None:
    assert "const esc = (value)" in PAGE


def test_the_page_never_uses_browser_prompts() -> None:
    assert "prompt(" not in PAGE and "alert(" not in PAGE


def test_the_page_refreshes_sessions() -> None:
    assert "grant_type=refresh_token" in PAGE


def test_previews_stay_brand_neutral() -> None:
    lowered = PAGE.lower()
    for name in ("instagram", "facebook", "tiktok", "linkedin"):
        assert name not in lowered


def test_the_page_offers_the_canva_export() -> None:
    assert "/canva.pptx?aspect=" in PAGE
