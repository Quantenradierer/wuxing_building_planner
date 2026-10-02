import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest

from roomplanner.errors import InfeasibleError
from roomplanner.serialization import from_dict
from roomplanner.ui.server import (
    RequestError,
    foundry_zip,
    generate_response,
    make_server,
    options,
)

PARAMS = {"building_type": "office", "width": 28, "depth": 18, "floors_above": 2, "seed": 5}


def test_options_list_every_choice() -> None:
    choices = options()
    assert "nightclub" in choices["building_types"]
    assert "neon" in choices["themes"]
    assert choices["sides"] == ["N", "E", "S", "W"]
    assert "coffin_hall" in choices["rooms"]["coffin_block"]


def test_response_has_the_building_and_one_image_per_floor() -> None:
    response = generate_response({"params": PARAMS, "cell_px": 6})
    building = from_dict(response["building"])
    assert building.seed == 5
    assert set(response["images"]) == {"0", "1"}
    assert response["images"]["0"].startswith("data:image/webp;base64,")


def test_invalid_params_are_request_errors() -> None:
    with pytest.raises(RequestError, match="width"):
        generate_response({"params": {**PARAMS, "width": "wide"}})
    with pytest.raises(RequestError, match="cell_px"):
        generate_response({"params": PARAMS, "cell_px": 1000})


def test_infeasible_buildings_raise() -> None:
    with pytest.raises(InfeasibleError):
        generate_response({"params": {**PARAMS, "width": 2}})


def test_foundry_zip_has_one_folder_with_scenes_images_and_macro() -> None:
    name, data = foundry_zip({"params": PARAMS})
    assert name == "office_5.zip"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = set(archive.namelist())
        scenes = json.loads(archive.read("office_5/scenes.json"))
    assert files == {
        "office_5/office_5_F0.png",
        "office_5/office_5_F1.png",
        "office_5/scenes.json",
        "office_5/import-macro.js",
    }
    assert [s["background"]["src"] for s in scenes] == [
        "roomplanner/office_5/office_5_F0.png",
        "roomplanner/office_5/office_5_F1.png",
    ]


@pytest.fixture
def base_url() -> Iterator[str]:
    server = make_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _post(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_server_serves_page_and_api(base_url: str) -> None:
    with urllib.request.urlopen(base_url + "/") as response:
        assert b"Roomplanner" in response.read()
    status, body = _post(base_url + "/api/generate", {"params": PARAMS, "cell_px": 6})
    assert status == 200
    assert body["building"]["seed"] == 5
    status, body = _post(base_url + "/api/generate", {"params": {**PARAMS, "width": 2}})
    assert status == 422
    assert "at least" in body["error"]


def test_server_serves_the_foundry_zip(base_url: str) -> None:
    request = urllib.request.Request(
        base_url + "/api/export/foundry",
        json.dumps({"params": PARAMS}).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        assert response.headers["Content-Type"] == "application/zip"
        assert 'filename="office_5.zip"' in response.headers["Content-Disposition"]
        assert zipfile.is_zipfile(io.BytesIO(response.read()))
