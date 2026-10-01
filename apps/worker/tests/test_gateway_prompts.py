from __future__ import annotations

import hashlib

import pytest
from pydantic import BaseModel
from stratum_worker.gateway import (
    BackgroundRequest,
    CaptionRequest,
    EditParseRequest,
    PostCopyRequest,
    TaskName,
)
from stratum_worker.gateway.prompts import VERSION, load, read_text, render

# sha256 of each prompt file at its current VERSION. Editing a prompt means bumping
# VERSION[task] in prompts/__init__.py AND updating this table - never just one.
PINNED: dict[tuple[TaskName, int], str] = {
    (TaskName.POST_COPY, 1): "405fa711bbc466dd1fc494cbd3d0ca1510f62bec5621d546d59ea645420b8215",
    (
        TaskName.CAPTION_GENERATE,
        1,
    ): "9d7292ec7d2c51bde6d9cb19c422c180f91dfd7b90c4e9bfc002db90b33fe1ec",
    (TaskName.EDIT_PARSE, 5): "f572f3fd6c5d5bc8d02dbfd7157f728f44b1c37e856dbceece18a858555d8c6f",
    (
        TaskName.BACKGROUND_CREATE,
        1,
    ): "41bb621e0f0fe3b27464617112b7fcb20946962312011faf6a79658fc9ed4a11",
}

REQUESTS: dict[TaskName, BaseModel] = {
    TaskName.POST_COPY: PostCopyRequest(
        brief="20% off boiler servicing, book before 31 October.",
        client_name="Kowalski Plumbing",
        trade="plumbing_heating",
        services=("boiler service", "emergency plumber"),
        areas=("Manchester", "Didsbury"),
        allowed_hashtags=("#manchester", "#plumbing"),
        must_include=("Gas Safe 000000",),
        banned_claims=("cheapest in Manchester",),
        photo_description="an engineer kneeling beside a wall-mounted boiler",
    ),
    TaskName.CAPTION_GENERATE: CaptionRequest(
        brief="Winter boiler checks",
        client_name="Northgate",
        banned_words=["cheap"],
        past_posts=["Old post"],
    ),
    TaskName.EDIT_PARSE: EditParseRequest(
        message="bigger logo", layers={"logo": "logo image"}, palette=["palette.primary"]
    ),
    TaskName.BACKGROUND_CREATE: BackgroundRequest(prompt="a tidy garage workshop"),
}


@pytest.mark.parametrize("task", list(TaskName))
def test_prompt_text_is_pinned_to_its_version(task: TaskName) -> None:
    digest = hashlib.sha256(read_text(task).encode()).hexdigest()
    assert PINNED.get((task, VERSION[task])) == digest, (
        f"{task} prompt changed: bump VERSION[{task!s}] and pin the new hash {digest}"
    )


@pytest.mark.parametrize("task", list(TaskName))
def test_every_placeholder_is_filled(task: TaskName) -> None:
    rendered = render(task, REQUESTS[task])
    assert "$" not in rendered.user
    assert rendered.id == f"{task}@v{VERSION[task]}"


def test_caption_prompt_carries_request_data() -> None:
    rendered = render(TaskName.CAPTION_GENERATE, REQUESTS[TaskName.CAPTION_GENERATE])
    assert "Winter boiler checks" in rendered.user
    assert "cheap" in rendered.user
    assert "JSON" in rendered.system


def test_edit_prompt_lists_layers_and_palette() -> None:
    rendered = render(TaskName.EDIT_PARSE, REQUESTS[TaskName.EDIT_PARSE])
    assert '"logo"' in rendered.user and "palette.primary" in rendered.user


def test_background_prompt_forbids_text() -> None:
    prompt = load(TaskName.BACKGROUND_CREATE)
    assert prompt.system == ""
    assert (
        "no text"
        in render(TaskName.BACKGROUND_CREATE, REQUESTS[TaskName.BACKGROUND_CREATE]).user.lower()
    )


def test_client_text_with_dollar_signs_is_inserted_verbatim() -> None:
    request = CaptionRequest(brief="Save $50 on ${service} today", client_name="Northgate")
    assert "Save $50 on ${service} today" in render(TaskName.CAPTION_GENERATE, request).user


def test_wrong_request_type_is_refused() -> None:
    with pytest.raises(TypeError):
        render(TaskName.EDIT_PARSE, REQUESTS[TaskName.CAPTION_GENERATE])


def test_post_copy_prompt_carries_rules_and_limits() -> None:
    rendered = render(TaskName.POST_COPY, REQUESTS[TaskName.POST_COPY])
    assert "Gas Safe 000000" in rendered.user
    assert "cheapest in Manchester" in rendered.user
    assert "#manchester" in rendered.user
    assert "boiler service" in rendered.user and "Didsbury" in rendered.user
    assert "an engineer kneeling" in rendered.user
    # the first-sentence rule is the discoverability lever; it must be in the prompt
    assert "first sentence" in rendered.system.lower()
    assert "JSON" in rendered.system


def test_post_copy_prompt_omits_alt_text_when_no_photo_description() -> None:
    """Inventing a description of a photo nobody looked at is worse than none."""
    request = REQUESTS[TaskName.POST_COPY].model_copy(update={"photo_description": ""})
    rendered = render(TaskName.POST_COPY, request)
    assert "(no photo description)" in rendered.user
