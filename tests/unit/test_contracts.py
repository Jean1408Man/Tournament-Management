"""Casos de contrato independientes de red, disco y middleware."""

import json
from dataclasses import replace

import pytest

from tournament_management.shared.messages import (
    AttemptOutcome,
    AttemptSpec,
    DomainEvent,
    MatchSpec,
    OutcomeKind,
    WorkerInfo,
    WorkerStatus,
)
from tournament_management.shared.states import (
    TRANSITIONS,
    AttemptState,
    MatchState,
    TournamentState,
    transition,
)


def match():
    return MatchSpec(
        tournament_id="t1",
        match_id="m1",
        competitor_ids=("a", "b"),
        artifact_hashes=("a" * 64, "b" * 64),
        initial_piles=(3, 5, 7),
        first_player=0,
        seed=42,
    )


def attempt():
    return AttemptSpec(
        attempt_id="a1",
        generation=1,
        match=match(),
        turn_timeout_ms=100,
        match_timeout_ms=1000,
        memory_limit_bytes=1000000,
        output_limit_bytes=1024,
    )


def outcome(kind=OutcomeKind.COMPLETED, winner="a"):
    return AttemptOutcome(
        match_id="m1",
        attempt_id="a1",
        generation=1,
        worker_id="w1",
        kind=kind,
        winner_id=winner,
        reason="test",
    )


@pytest.mark.parametrize(
    "message",
    [
        match(),
        attempt(),
        outcome(),
        outcome(OutcomeKind.BOT_FAILURE),
        outcome(OutcomeKind.INFRASTRUCTURE_FAILURE, ""),
        outcome(OutcomeKind.NO_WINNER, ""),
        WorkerInfo(worker_id="w1", status=WorkerStatus.AVAILABLE, capacity=2, running_attempts=1),
        DomainEvent(
            event_id="e1",
            tournament_id="t1",
            match_id="m1",
            attempt_id="a1",
            event_type="result.accepted",
            sequence=1,
            occurred_at="2026-10-17T12:00:00+00:00",
            detail="accepted",
        ),
    ],
)
def test_json_roundtrip(message):
    restored = type(message).from_json(message.to_json())
    assert restored == message
    assert type(restored) is type(message)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"first_player": True},
        {"initial_piles": [0]},
        {"competitor_ids": ["a", "a"]},
        {"seed": "42"},
        {"extra": 1},
    ],
)
def test_rejects_invalid_wire_messages(changes):
    data = json.loads(match().to_json())
    data.update(changes)
    with pytest.raises(ValueError):
        MatchSpec.from_json(json.dumps(data))


def test_rejects_missing_field_and_mutable_constructor():
    data = json.loads(match().to_json())
    del data["schema_version"]
    with pytest.raises(ValueError):
        MatchSpec.from_dict(data)
    with pytest.raises(ValueError):
        replace(match(), initial_piles=[1, 2])


@pytest.mark.parametrize(
    "kind,winner",
    [
        (OutcomeKind.INFRASTRUCTURE_FAILURE, "a"),
        (OutcomeKind.NO_WINNER, "a"),
        (OutcomeKind.COMPLETED, ""),
        (OutcomeKind.BOT_FAILURE, ""),
    ],
)
def test_rejects_incompatible_winners(kind, winner):
    with pytest.raises(ValueError):
        outcome(kind, winner)


def test_result_identity_and_generation():
    outcome().validate_for(attempt())
    for invalid in [
        replace(outcome(), attempt_id="old"),
        replace(outcome(), generation=2),
        replace(outcome(), match_id="other"),
        replace(outcome(), winner_id="outsider"),
    ]:
        with pytest.raises(ValueError):
            invalid.validate_for(attempt())


@pytest.mark.parametrize("enum", [TournamentState, MatchState, AttemptState])
def test_all_state_pairs_follow_explicit_table(enum):
    for current in enum:
        for target in enum:
            allowed = target.value in TRANSITIONS[enum].get(current.value, set())
            if allowed:
                assert transition(current, target) is target
            else:
                with pytest.raises(ValueError):
                    transition(current, target)


def test_different_entities_cannot_share_a_transition():
    with pytest.raises(ValueError):
        transition(TournamentState.RUNNING, MatchState.COMPLETED)


@pytest.mark.parametrize(
    "state",
    [AttemptState.LOST, AttemptState.EXPIRED, AttemptState.CANCELLED, AttemptState.ACCEPTED],
)
def test_late_result_does_not_reopen_terminal_attempt(state):
    with pytest.raises(ValueError):
        transition(state, AttemptState.ACCEPTED)


def test_retry_retains_match_and_changes_identity():
    original = attempt()
    retry = replace(original, attempt_id="a2", generation=2)
    assert retry.match == original.match
    assert retry.attempt_id != original.attempt_id
    with pytest.raises(ValueError):
        outcome().validate_for(retry)
