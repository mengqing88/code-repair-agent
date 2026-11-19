from attrs.validators import disabled, get_disabled


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
