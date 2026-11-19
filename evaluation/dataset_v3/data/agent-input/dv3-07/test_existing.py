"""Existing behaviour of the deduper."""

from dedup import Deduper


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def test_the_first_event_is_new():
    clock = FakeClock()
    assert Deduper(10, clock).seen("a") is False


def test_a_repeat_inside_the_window_is_suppressed():
    clock = FakeClock()
    deduper = Deduper(10, clock)
    deduper.seen("a")
    clock.now = 4
    assert deduper.seen("a") is True


def test_a_repeat_after_the_window_is_new_again():
    clock = FakeClock()
    deduper = Deduper(10, clock)
    deduper.seen("a")
    clock.now = 20
    assert deduper.seen("a") is False
