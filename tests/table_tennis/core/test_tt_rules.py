import pytest

from table_tennis.core.rules import game_winner, next_server


@pytest.mark.parametrize("p1,p2,expected", [
    (0, 0, "p1"), (1, 0, "p1"), (2, 0, "p2"), (3, 2, "p1"),
    (10, 10, "p1"), (11, 10, "p2"), (11, 11, "p1"), (11, 5, None), (12, 10, None),
])
def test_next_server_contract_examples(p1, p2, expected):
    assert next_server(p1, p2, "p1") == expected


def test_server_sequence_first_20_points():
    # Points alternate between players so no one wins before 10:10.
    seq = []
    a = b = 0
    for i in range(21):
        seq.append(next_server(a, b, "p1"))
        if i % 2 == 0:
            a += 1
        else:
            b += 1
    expected = [("p1" if (t // 2) % 2 == 0 else "p2") for t in range(20)] + ["p1"]
    assert seq == expected  # total 20 = 10:10 -> p1


def test_next_server_first_server_p2_mirrors():
    assert next_server(0, 0, "p2") == "p2"
    assert next_server(2, 0, "p2") == "p1"
    assert next_server(11, 10, "p2") == "p1"


def test_next_server_unknown_player():
    with pytest.raises(ValueError):
        next_server(0, 0, "p3")


@pytest.mark.parametrize("p1,p2,winner", [
    (10, 9, None), (11, 9, "p1"), (11, 10, None), (12, 10, "p1"), (11, 11, None), (9, 11, "p2"), (0, 0, None),
])
def test_game_winner(p1, p2, winner):
    assert game_winner(p1, p2) == winner


@pytest.mark.parametrize("p1,p2", [(-1, 0), (0, -1)])
def test_game_winner_negative_raises(p1, p2):
    with pytest.raises(ValueError):
        game_winner(p1, p2)
