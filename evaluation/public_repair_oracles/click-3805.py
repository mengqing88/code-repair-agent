import pickle

from click import Option


def run():
    explicit = Option(["--value"], default="kept", flag_value="flag")
    assert pickle.loads(pickle.dumps(explicit)).default == "kept"

    implicit = Option(["--value"])
    try:
        copied = pickle.loads(pickle.dumps(implicit))
    except ValueError as error:
        if "is not a valid Sentinel" not in str(error):
            raise
        raise SystemExit("EXPECTED_PUBLIC_REPAIR_DEFECT") from None
    assert copied is not implicit
    assert copied.name == implicit.name
    assert copied.default is implicit.default


try:
    run()
except SystemExit:
    raise
except Exception:
    raise SystemExit("PUBLIC_REPAIR_CANDIDATE_FAILED") from None
print("PUBLIC_REPAIR_ORACLE_PASSED")
