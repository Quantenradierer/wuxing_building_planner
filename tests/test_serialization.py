import json

import pytest

from roomplanner.errors import SchemaError
from roomplanner.generator import generate
from roomplanner.serialization import from_json, to_dict, to_json

from .conftest import make_params


def test_round_trip() -> None:
    building = generate(make_params(floors_above=2, floors_below=1))
    assert any(o.sliding for f in building.floors for o in f.openings)  # elevator doors
    assert from_json(to_json(building)) == building


def test_document_shape() -> None:
    document = to_dict(generate(make_params()))
    assert document["schema_version"] == 3
    assert document["cell_size_m"] == 0.5
    door = next(o for o in document["floors"][0]["openings"] if o["kind"] == "door")
    assert {"kind", "edges", "swing"} <= set(door)
    assert door["edges"][0][2] in {"h", "v"}


def test_reads_version_2_documents() -> None:
    building = generate(make_params())
    document = to_dict(building)
    document["schema_version"] = 2
    for floor in document["floors"]:
        del floor["devices"], floor["lights"]
    assert from_json(json.dumps(document)).floors[0].rooms == building.floors[0].rooms


def test_rejects_other_schema_versions() -> None:
    document = to_dict(generate(make_params()))
    document["schema_version"] = 99
    with pytest.raises(SchemaError, match="schema_version"):
        from_json(json.dumps(document))


def test_rejects_malformed_documents() -> None:
    document = to_dict(generate(make_params()))
    del document["floors"][0]["walls"]
    with pytest.raises(SchemaError, match="malformed"):
        from_json(json.dumps(document))
