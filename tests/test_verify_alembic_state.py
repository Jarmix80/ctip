"""Testy kontroli rewizji Alembic dla stosu testowego."""

from scripts.verify_alembic_state import revisions_from_output


def test_revisions_from_output_ignores_descriptions_and_info() -> None:
    """Parser rozpoznaje rewizje niezależnie od dopisków Alembic."""
    output = """INFO pomijane
e4a8c1d9f2b7 (head)
71C4E8A2D9F0 -> a6f3c8d2e910 (head), opis
"""

    assert revisions_from_output(output) == {
        "e4a8c1d9f2b7",
        "71c4e8a2d9f0",
    }
