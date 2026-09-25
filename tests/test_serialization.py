import json

import pytest

from roomplanner.errors import SchemaError
from roomplanner.generator import generate
from roomplanner.serialization import from_json, to_dict, to_json

from .conftest import make_params


def test_round_trip() -> None:
    building = generate(make_params(floors_above=2, floors_below=1))
    assert from_json(to_json(building)) == building


def test_document_shape() -> None:
    document = to_dict(generate(make_params()))
    assert document["schema_version"] == 1
    assert document["cell_size_m"] == 0.5
    door = next(o for o in document["floors"][0]["openings"] if o["kind"] == "door")
    assert set(door) == {"kind", "edges", "swing"}
    assert door["edges"][0][2] in {"h", "v"}


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
