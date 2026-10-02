"""Versioned prompt registry. Prompt text lives in the .md files next to this one.

Bump VERSION[task] whenever a prompt file changes, so evals and traces can tell
prompt versions apart. A test pins each file's hash to its version, so an edit
without a bump fails CI.

File format: an optional HTML comment, then `## system` (optional) and `## user`
sections. The user section is a `string.Template`: `$name` placeholders are filled
from the task request. Values are inserted as data and never re-interpreted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from string import Template

from pydantic import BaseModel

from stratum_worker.gateway.tasks import (
    BackgroundRequest,
    CaptionRequest,
    EditParseRequest,
    PostCopyRequest,
    TaskName,
)

VERSION: dict[TaskName, int] = {
    TaskName.POST_COPY: 2,
    TaskName.CAPTION_GENERATE: 1,
    TaskName.EDIT_PARSE: 5,
    TaskName.BACKGROUND_CREATE: 1,
}

FILES: dict[TaskName, str] = {
    TaskName.POST_COPY: "post_copy.md",
    TaskName.CAPTION_GENERATE: "caption_generate.md",
    TaskName.EDIT_PARSE: "edit_parse.md",
    TaskName.BACKGROUND_CREATE: "background_create.md",
}

_SECTIONS = ("## system", "## user")


@dataclass(frozen=True)
class Prompt:
    task: TaskName
    version: int
    system: str
    user: Template

    @property
    def id(self) -> str:
        return f"{self.task}@v{self.version}"


@dataclass(frozen=True)
class RenderedPrompt:
    id: str
    system: str
    user: str


def read_text(task: TaskName) -> str:
    return files(__name__).joinpath(FILES[task]).read_text(encoding="utf-8")


def _parse(task: TaskName, text: str) -> Prompt:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.strip() in _SECTIONS:
            current = line.strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    if "## user" not in sections:
        raise ValueError(f"prompt {FILES[task]} has no '## user' section")
    system = "\n".join(sections.get("## system", [])).strip()
    user = "\n".join(sections["## user"]).strip()
    return Prompt(task=task, version=VERSION[task], system=system, user=Template(user))


@cache
def load(task: TaskName) -> Prompt:
    return _parse(task, read_text(task))


def _list(values: tuple[str, ...]) -> str:
    return ", ".join(values) or "(none)"


def _values(task: TaskName, request: BaseModel) -> dict[str, str]:
    if task is TaskName.POST_COPY and isinstance(request, PostCopyRequest):
        return {
            "client_name": request.client_name,
            "trade": request.trade or "(not given)",
            "tone": request.tone,
            "language": request.language,
            "platform": request.platform,
            "services": _list(request.services),
            "areas": _list(request.areas),
            "allowed_hashtags": _list(request.allowed_hashtags),
            "max_hashtags": str(request.max_hashtags),
            "must_include": _list(request.must_include),
            "banned_claims": _list(request.banned_claims),
            "photo_description": request.photo_description or "(no photo description)",
            "headline_max_chars": str(request.headline_max_chars),
            "headline_short_max_chars": str(max(10, round(request.headline_max_chars * 0.6))),
            "headline_long_max_chars": str(min(180, round(request.headline_max_chars * 1.5))),
            "offer_max_chars": str(request.offer_max_chars),
            "cta_max_chars": str(request.cta_max_chars),
            "brief": request.brief,
        }
    if task is TaskName.CAPTION_GENERATE and isinstance(request, CaptionRequest):
        return {
            "client_name": request.client_name,
            "platform": request.platform,
            "tone": request.tone,
            "language": request.language,
            "banned_words": ", ".join(request.banned_words) or "(none)",
            "past_posts": "\n---\n".join(request.past_posts) or "(none)",
            "brief": request.brief,
        }
    if task is TaskName.EDIT_PARSE and isinstance(request, EditParseRequest):
        colours = request.colours or {token: token for token in request.palette}
        return {
            "layers": json.dumps(request.layers, ensure_ascii=False, indent=2),
            "colours": "\n".join(f"{token}: {about}" for token, about in colours.items())
            or "(none)",
            "asked": "\n".join(request.asked) or "(no colour named)",
            "design": request.design or "(no style settings)",
            "recent": "\n".join(request.recent) or "(none)",
            "message": request.message,
        }
    if task is TaskName.BACKGROUND_CREATE and isinstance(request, BackgroundRequest):
        return {"prompt": request.prompt.rstrip(". "), "aspect": request.aspect}
    raise TypeError(f"{task} cannot render a {type(request).__name__}")


def render(task: TaskName, request: BaseModel) -> RenderedPrompt:
    prompt = load(task)
    user = prompt.user.substitute(_values(task, request))
    return RenderedPrompt(id=prompt.id, system=prompt.system, user=user)


__all__ = ["FILES", "VERSION", "Prompt", "RenderedPrompt", "load", "read_text", "render"]
