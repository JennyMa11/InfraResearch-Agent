from infraresearch.provider import strip_reasoning


def test_reasoning_blocks_are_removed_from_visible_answer() -> None:
    text = (
        "<think>\nPrivate chain of thought that must not be displayed.\n</think>\n\n"
        "Prefix caching reuses KV blocks [S1]."
    )
    assert strip_reasoning(text) == "Prefix caching reuses KV blocks [S1]."


def test_unfinished_reasoning_is_not_exposed() -> None:
    assert strip_reasoning("<think>private unfinished reasoning") == ""


def test_plain_final_answer_is_preserved() -> None:
    assert strip_reasoning("Direct answer [S1].") == "Direct answer [S1]."
