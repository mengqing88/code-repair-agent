from target import *

def _echo_identity():
    service = IdempotentService(lambda payload: 'v' + str(payload))
    return service.handle('k', 1)

def _twice_same():
    service = IdempotentService(lambda payload: 'v' + str(payload))
    return [service.handle('k', 1), service.handle('k', 1)]

def _twice_diff_payload():
    service = IdempotentService(lambda payload: 'v' + str(payload))
    return [service.handle('k', 1), service.handle('k', 2)]

def _handler_calls():
    box = {'n': 0}
    def handler(payload):
        box['n'] += 1
        return payload
    service = IdempotentService(handler)
    service.handle('k', 1)
    service.handle('k', 2)
    return box['n']

def _distinct_keys():
    service = IdempotentService(lambda payload: 'v' + payload)
    return [service.handle('a', 'a'), service.handle('b', 'b')]

def _handler_calls_after_three_replays():
    box = {'n': 0}
    def handler(payload):
        box['n'] += 1
        return payload
    service = IdempotentService(handler)
    for payload in (1, 2, 3):
        service.handle('k', payload)
    return box['n']


def test_existing_1():
    assert _echo_identity() == 'v1'


def test_existing_2():
    assert _twice_same() == ['v1', 'v1']


def test_existing_3():
    assert _distinct_keys() == ['va', 'vb']
