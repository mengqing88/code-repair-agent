def run_with_retry(make_call, max_retries):
    if max_retries < 0:
        raise ValueError('max_retries must be non-negative')
    error = None
    for _ in range(max_retries):
        try:
            return make_call()
        except Exception as exc:
            error = exc
    if error is not None:
        raise error
    raise RuntimeError('no attempt was made')
