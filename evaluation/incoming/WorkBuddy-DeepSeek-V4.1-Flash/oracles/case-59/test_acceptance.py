from target import *

def _buffer_first():
    return EventBuffer().append('a')

def _buffer_drain_all():
    buf = EventBuffer()
    buf.clear()
    buf.append('a')
    buf.append('b')
    return buf.drain()

def _buffer_cleared_after_clear():
    buf = EventBuffer()
    buf.append('a')
    buf.clear()
    return len(buf.events)

def _buffer_isolated():
    first = EventBuffer()
    second = EventBuffer()
    first.append('a')
    second.append('b')
    return [first.drain(), second.drain()]

def _buffer_no_shared():
    return EventBuffer().events is not EventBuffer().events

def _buffer_reset_after_drain():
    buf = EventBuffer()
    buf.append('a')
    buf.drain()
    return buf.append('b')

def _buffer_independent_of_initial():
    first = EventBuffer()
    first.append('a')
    return EventBuffer().append('b')


def test_acceptance_1():
    assert _buffer_isolated() == [['a'], ['b']]


def test_acceptance_2():
    assert _buffer_no_shared() is True


def test_acceptance_3():
    assert _buffer_independent_of_initial() == 1
