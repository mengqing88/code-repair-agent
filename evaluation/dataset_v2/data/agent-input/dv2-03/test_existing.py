"""Existing behaviour of the workflow."""

import pytest

from workflow import OrderWorkflow, TransitionError


def test_a_draft_can_be_submitted():
    flow = OrderWorkflow()
    assert flow.transition("submitted") == "submitted"


def test_cancelling_a_draft_moves_it_to_cancelled():
    flow = OrderWorkflow()
    assert flow.transition("cancelled") == "cancelled"
    assert flow.reminders == []


def test_an_unknown_state_is_rejected():
    flow = OrderWorkflow()
    with pytest.raises(TransitionError):
        flow.transition("archived")
