"""Existing behaviour of the retry executor."""

import pytest

from executor import BudgetExceeded, Executor


class FakeClock:
    def __init__(self, step):
        self.now = 0
        self.step = step

    def __call__(self):
        return self.now

    def tick(self):
        self.now += self.step


def test_a_successful_task_is_run_once():
    clock = FakeClock(1)
    executor = Executor(clock)
    assert executor.run(lambda: "ok", budget_seconds=10) == "ok"


def test_a_task_can_succeed_on_the_last_attempt():
    clock = FakeClock(1)
    executor = Executor(clock)
    calls = []

    def work():
        calls.append(1)
        clock.tick()
        if len(calls) == 3:
            return "done"
        raise RuntimeError("boom")

    assert executor.run(work, budget_seconds=100) == "done"


def test_an_exhausted_retry_reports_the_error():
    clock = FakeClock(0)
    executor = Executor(clock)
    with pytest.raises(BudgetExceeded) as excinfo:
        executor.run(lambda: (_ for _ in ()).throw(RuntimeError("boom")), budget_seconds=10)
    assert excinfo.value.attempts == 3
