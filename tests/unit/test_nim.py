import pytest

from tournament_management.game.nim import (
    Move,
    Position,
    apply_move,
    generate_position,
    initial_position,
    is_terminal,
    validate_move,
    winner,
)


def test_valid_move_reduces_one_pile_and_changes_turn():
    position = initial_position((3, 5), first_player=1)
    result = apply_move(position, Move(pile=1, count=2))
    assert result == Position((3, 3), player=0)
    assert result.piles[0] == position.piles[0]
    assert result.piles[1] < position.piles[1]


@pytest.mark.parametrize("move", [Move(2, 1), Move(0, 4)])
def test_invalid_moves_are_rejected(move):
    with pytest.raises(ValueError):
        validate_move(initial_position((3, 2)), move)


@pytest.mark.parametrize("count", [0, -1])
def test_move_requires_positive_count(count):
    with pytest.raises(ValueError):
        Move(0, count)


def test_normal_nim_winner_is_last_player_to_move():
    position = initial_position((1,), first_player=0)
    terminal = apply_move(position, Move(0, 1))
    assert is_terminal(terminal)
    assert winner(terminal) == 0
    assert winner(position) is None


@pytest.mark.parametrize("piles", [(), (0,), (1, 0)])
def test_initial_position_requires_positive_piles(piles):
    with pytest.raises(ValueError):
        initial_position(piles)


def test_seeded_positions_are_reproducible_without_shared_random_state():
    first = generate_position(42, pile_count=4, min_pile=1, max_pile=9)
    assert first == generate_position(42, pile_count=4, min_pile=1, max_pile=9)
    assert first != generate_position(43, pile_count=4, min_pile=1, max_pile=9)


def test_generated_position_can_be_reused_for_multiple_attempts():
    position = generate_position(7)
    apply_move(position, Move(0, 1))
    assert position == generate_position(7)
