from ccgram.display_buffer import DisplayBuffer


def test_buffer_appends_diffs_and_keeps_oldest_complete_lines() -> None:
    buffer = DisplayBuffer(max_chars=12)

    assert not buffer.update("one")
    assert buffer.update("one\ntwo")
    assert buffer.text() == "two"

    assert buffer.update("one\ntwo\nthree\nfour")
    assert buffer.text() == "three\nfour"


def test_buffer_does_not_change_for_unchanged_screen() -> None:
    buffer = DisplayBuffer(max_chars=12)

    buffer.update("one")
    assert not buffer.update("one")
    assert buffer.text() == ""
