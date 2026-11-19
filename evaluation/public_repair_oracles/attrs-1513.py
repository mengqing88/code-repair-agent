from attrs.validators import disabled, get_disabled


def run():
    assert not get_disabled()
    with disabled():
        assert get_disabled()
    assert not get_disabled()

    with disabled():
        try:
            with disabled():
                assert get_disabled()
                raise LookupError("context body failed")
        except LookupError:
            if not get_disabled():
                raise SystemExit("EXPECTED_PUBLIC_REPAIR_DEFECT") from None
    assert not get_disabled()


try:
    run()
except SystemExit:
    raise
except Exception:
    raise SystemExit("PUBLIC_REPAIR_CANDIDATE_FAILED") from None
print("PUBLIC_REPAIR_ORACLE_PASSED")
