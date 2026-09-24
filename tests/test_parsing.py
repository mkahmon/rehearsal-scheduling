from __future__ import annotations

import json

import pytest

from rehearsal.model import ProblemError
from rehearsal.parsing import load_problem, problem_from_dict

MINIMAL = {
    "day": {"start": "09:00", "end": "11:00"},
    "rooms": {"Hall": "all"},
    "people": {"Ana": ["09:00-10:00"]},
    "pieces": [{"name": "X", "room": "Hall", "players": ["Ana"]}],
}


def test_minimal_document():
    problem = problem_from_dict(MINIMAL)
    assert problem.grid.n_slots == 4
    assert problem.rooms["Hall"].available == frozenset({0, 1, 2, 3})
    assert problem.people["Ana"].available == frozenset({0, 1})
    assert problem.pieces[0].duration == 1, "slots defaults to 1"


def test_room_availability_forms_are_equivalent():
    for spec in ["all", None, ["09:00-11:00"], "09:00-11:00"]:
        doc = {**MINIMAL, "rooms": {"Hall": spec}}
        assert problem_from_dict(doc).rooms["Hall"].available == frozenset(range(4))


def test_people_may_be_a_bare_list():
    doc = {**MINIMAL, "people": ["Ana", "Ben"]}
    problem = problem_from_dict(doc)
    assert problem.people["Ben"].available == frozenset(range(4))


def test_player_not_listed_under_people_defaults_to_fully_available():
    doc = {**MINIMAL, "pieces": [{"name": "X", "room": "Hall", "players": ["Ana", "Zoe"]}]}
    problem = problem_from_dict(doc)
    assert problem.people["Zoe"].available == frozenset(range(4))


def test_multiple_windows_union():
    doc = {**MINIMAL, "people": {"Ana": ["09:00-09:30", "10:30-11:00"]}}
    assert problem_from_dict(doc).people["Ana"].available == frozenset({0, 3})


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"day": None}, "day"),
        ({"rooms": {}}, "rooms"),
        ({"pieces": []}, "pieces"),
        ({"pieces": [{"room": "Hall"}]}, "name"),
        ({"pieces": [{"name": "X"}]}, "room"),
        ({"pieces": [{"name": "X", "room": "Hall", "slots": "two"}]}, "whole number"),
    ],
)
def test_structural_errors_name_the_problem(mutation, message):
    with pytest.raises(ProblemError, match=message):
        problem_from_dict({**MINIMAL, **mutation})


def test_json_input_works_too(tmp_path):
    path = tmp_path / "problem.json"
    path.write_text(json.dumps(MINIMAL))
    problem, warnings = load_problem(path)
    assert problem.pieces[0].name == "X"
    assert warnings == []


def test_load_reports_missing_file(tmp_path):
    with pytest.raises(ProblemError, match="cannot read"):
        load_problem(tmp_path / "nope.yaml")


def test_load_reports_bad_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("day: {start: '09:00'\npieces: [")
    with pytest.raises(ProblemError, match="cannot parse"):
        load_problem(path)
