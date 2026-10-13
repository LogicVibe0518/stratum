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


def test_the_page_reads_and_saves_brand_colours() -> None:
    assert "/brand/suggestion" in PAGE
    assert '"PUT"' in PAGE


def test_new_clients_are_not_given_a_hard_coded_brand() -> None:
    assert "#ff6a00" not in PAGE.lower()


def test_the_page_handles_best_three_options() -> None:
    assert "best_three" in PAGE
    assert "Best three (Stratum chooses)" in PAGE
    assert 'if (id === "best_three") return "Best three"' in PAGE
    assert "/options/" in PAGE
    assert "/pick" in PAGE
    assert "paintOptions" in PAGE
    assert "Switching…" in PAGE


def test_option_cards_do_not_put_server_strings_into_markup() -> None:
    card = PAGE.split("function optionCard")[1].split("function pickOption")[0]
    assert "innerHTML" not in card
    assert "textContent" in card
    assert 'setAttribute("aria-label"' in card


def test_the_page_gained_no_new_external_asset() -> None:
    assert PAGE.count("https://fonts.googleapis.com") == 2
    assert PAGE.count("<script src") == 0
    assert "cdn." not in PAGE.lower()


def _function_body(name: str) -> str:
    start = PAGE.split(f"function {name}")[1]
    return start.split("\nfunction ")[0]


def test_history_results_are_in_words() -> None:
    assert "Asked you" in PAGE
    assert "Didn't work" in PAGE
    assert '"Done"' in PAGE


def test_choice_buttons_do_not_put_server_strings_into_markup() -> None:
    body = _function_body("choiceButtons")
    assert "innerHTML" not in body
    assert "textContent" in body


def test_the_page_has_the_control_panel() -> None:
    assert "/inspect" in PAGE
    assert "/edit" in PAGE
    assert "function sendEdit" in PAGE
    assert "function safeHex" in PAGE
    assert "Asked you" in PAGE
    assert "Didn't work" in PAGE


def test_safe_hex_only_accepts_six_digit_hex() -> None:
    assert "^#[0-9a-f]{6}$" in PAGE


def test_editor_builders_do_not_put_server_strings_into_markup() -> None:
    for name in (
        "paintEditor",
        "layerRow",
        "paintInspector",
        "paintStyle",
        "paintMeasure",
        "measureOverlay",
        "choiceButtons",
    ):
        body = _function_body(name)
        assert "innerHTML" not in body, name


def test_the_page_has_the_taste_lab() -> None:
    assert "/api/lab/next" in PAGE
    assert "/answer" in PAGE
    assert "/api/lab/progress" in PAGE
    assert "Which would you send the client?" in PAGE


def test_lab_builders_do_not_put_server_strings_into_markup() -> None:
    for name in ("paintLab", "labCard", "labReasons"):
        body = _function_body(name)
        assert "innerHTML" not in body, name
        assert "textContent" in body or "setAttribute" in body, name


def test_lab_keys_leave_browser_shortcuts_alone() -> None:
    body = _function_body("onLabKey")
    assert "metaKey" in body and "ctrlKey" in body


def test_lab_functions_do_not_read_ranking_fields() -> None:
    for name in ("paintLab", "labCard", "labReasons"):
        body = _function_body(name)
        for word in ("bucket", "rank", "score", "family"):
            assert word not in body, f"{name} reads {word}"


def test_brief_gaps_do_not_put_server_strings_into_markup() -> None:
    body = _function_body("paintBriefGaps")
    assert "innerHTML" not in body
    assert "textContent" in body


def test_the_brief_box_checks_while_you_type() -> None:
    assert "/brief/check" in PAGE
    assert "AbortController" in PAGE


def test_the_post_shows_questions_for_the_client() -> None:
    assert "words.missing" in PAGE
    assert "Ask the client" in PAGE


def test_tweak_body_includes_pointed_only_when_set() -> None:
    body = _function_body("tweakBody")
    assert "pointed" in body
    assert "if (POINTING)" in body
    assert "message" in body


def test_pointing_chip_uses_text_content() -> None:
    body = _function_body("paintPointing")
    assert "textContent" in body
    assert "innerHTML" not in body


def test_escape_clears_the_pointing() -> None:
    keys = PAGE.split('$("tweak").addEventListener("keydown"')[1].split("});")[0]
    assert "Escape" in keys
    assert "clearPointing" in keys


def body(name: str) -> str:
    """The source of one top-level function on the page."""
    start = PAGE.index(f"function {name}(")
    return PAGE[start : PAGE.index("\n}\n", start)]


def test_a_clients_page_shows_only_its_own_uploads() -> None:
    # I-17: Sparkle Squad's page listed Lone Star's uploads.
    assert '$("uploads").replaceChildren();' in body("openClient")


def test_a_message_typed_on_one_post_is_not_left_on_the_next() -> None:
    # I-18: "use 3 photos" typed for Lone Star showed on a Sparkle post.
    assert '$("tweak").value = "";' in body("openPost")


def test_a_short_name_skips_filler_words() -> None:
    # I-25: "first-time-booking-is" read as cut off mid-phrase.
    assert "SLUG_FILLER" in body("postSlug")
    for word in ('"is"', '"the"', '"about"', '"we"'):
        assert word in PAGE[PAGE.index("const SLUG_FILLER") : PAGE.index("function postSlug(")]


def test_options_say_when_the_current_design_is_none_of_them() -> None:
    # I-04: after a restyle no option card is "Shown", and nothing said why.
    painted = body("paintOptions")
    assert "Your current design is none of these" in painted
    assert "status.textContent = " in painted  # text, never markup


def test_making_lab_screens_says_how_many_posts_are_left() -> None:
    # I-29: the job reports posts it had no time for (`left`); the page dropped it.
    note = body("labMadeNote")
    assert "result.left" in note and "press Make more screens again" in note
    assert "result.skipped" in note
    assert "No posts with words and photos yet" not in PAGE  # wrong once every post had screens
    made = body("makeLabScreens")
    assert "labMadeNote(result)" in made and "openLab(note)" in made
    assert '$("labstatus").textContent = note' in body("openLab")
