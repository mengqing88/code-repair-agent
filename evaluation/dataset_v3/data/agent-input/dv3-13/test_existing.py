"""Existing behaviour of the migrator."""

from migrate import Migrator


def test_a_single_step_runs_once():
    migrator = Migrator()
    migrator.apply(1, lambda schema: schema.update({"a": 1}))
    assert migrator.schema == {"a": 1}
    assert migrator.version == 1


def test_the_schema_is_handed_to_the_step():
    seen = []
    Migrator().apply(1, lambda schema: seen.append(dict(schema)))
    assert seen == [{}]


def test_the_version_advances_step_by_step():
    migrator = Migrator()
    migrator.apply(1, lambda schema: schema.update({"a": 1}))
    migrator.apply(2, lambda schema: schema.update({"b": 2}))
    assert migrator.version == 2
