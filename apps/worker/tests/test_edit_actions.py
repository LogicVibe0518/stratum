"""The ops the service carries out itself (M10c), validated as strictly as spec ops.

Strict about what can happen (only restyle, ask and undo; settings are plain words),
forgiving about how a model says it: "Centre" is center, and a long question is
shortened rather than throwing away the whole answer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from stratum_worker.edit.actions import Ask, Restyle, Undo, is_undo, split, undo_asked


def test_spec_ops_and_service_ops_are_separated_in_order() -> None:
    raw = [
        {"op": "scale", "layer": "logo", "factor": 1.2},
        {"op": "restyle", "changes": {"fill": "surface"}},
        {"op": "undo"},
    ]
    spec_ops, actions = split(raw)
    assert spec_ops == [raw[0]]
    assert actions == [Restyle(changes={"fill": "surface"}), Undo()]


def test_a_model_may_spell_a_value_its_own_way() -> None:
    _, [restyle] = split([{"op": "restyle", "changes": {"Align": " Centre "}}])
    assert isinstance(restyle, Restyle) and restyle.changes == {"align": "center"}


def test_a_long_question_is_shortened_not_dropped() -> None:
    raw = {
        "op": "ask",
        "question": "Which? " * 100,
        "choices": [{"label": "Make the headline gold " * 10}] * 6,
    }
    _, [ask] = split([raw])
    assert isinstance(ask, Ask)
    assert len(ask.question) == 300 and len(ask.choices) == 4
    assert all(len(choice.label) <= 120 for choice in ask.choices)


@pytest.mark.parametrize(
    "raw",
    [
        {"op": "restyle", "changes": {}},
        {"op": "restyle", "changes": {"fill": "<script>"}},
        {"op": "restyle", "changes": {"fill": "surface"}, "extra": 1},
        {"op": "ask", "question": ""},
        {"op": "ask", "question": "Which?", "choices": [{"label": "x", "kind": "delete"}]},
        {"op": "undo", "to": 1},
        {"op": "undo", "all": "yes please"},
    ],
)
def test_anything_else_is_refused(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        split([raw])


@pytest.mark.parametrize("message", ["undo", "Undo that.", "go back", " REVERT! "])
def test_undo_is_recognised_without_a_model(message: str) -> None:
    assert is_undo(message)


@pytest.mark.parametrize("message", ["undo the logo change and make it bigger", "go back to blue"])
def test_a_longer_request_is_left_to_the_model(message: str) -> None:
    assert not is_undo(message)


@pytest.mark.parametrize(
    "message", ["undo all changes", "Start over.", "go back to the  original", "RESET"]
)
def test_undo_all_is_recognised_without_a_model(message: str) -> None:
    assert undo_asked(message) == Undo(all=True)


def test_one_step_and_all_steps_are_told_apart() -> None:
    assert undo_asked("undo") == Undo()
    assert split([{"op": "undo", "all": True}])[1] == [Undo(all=True)]
