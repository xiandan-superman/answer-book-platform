from __future__ import annotations

import base64
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import server as server_module
from app.llm_client import ImageGenerationResult, OpenAICompatibleClient
from app.settings import ProviderConfig


def _image_provider() -> ProviderConfig:
    return ProviderConfig(
        name="lingsuan_image",
        type="openai_compatible",
        base_url="https://lingsuan.org/v1",
        api_key="test-image-key",
        api_key_env="LINGSUAN_IMAGE_API_KEY",
        default_model="gpt-image-2",
        model_options=(),
        allow_custom_model=False,
        model_hint="",
        temperature=0.1,
        max_tokens=24576,
        image_model="gpt-image-2",
        image_model_options=("gpt-image-2",),
        image_size="1024x1024",
        supports_text_generation=False,
        supports_image_generation=True,
    )


def test_lingsuan_image_client_uses_openai_compatible_image_endpoint(tmp_path, monkeypatch) -> None:
    provider = _image_provider()
    client = OpenAICompatibleClient(provider)
    requests = []

    def fake_post(url, payload, timeout):
        requests.append((url, payload, timeout))
        return {"data": [{"b64_json": base64.b64encode(b"png-bytes").decode("ascii")}]}

    monkeypatch.setattr(client, "_post_json", fake_post)
    output = tmp_path / "generated.png"

    result = client.generate_image("draw a chart", output)

    assert result.model == "gpt-image-2"
    assert output.read_bytes() == b"png-bytes"
    assert requests[0][0] == "https://lingsuan.org/v1/images/generations"
    assert requests[0][1]["model"] == "gpt-image-2"
    assert requests[0][1]["size"] == "1024x1024"


def test_lingsuan_image_client_uses_edit_endpoint_with_original_reference_bytes(
    tmp_path,
) -> None:
    from PIL import Image

    provider = _image_provider()
    client = OpenAICompatibleClient(provider)
    first = tmp_path / "first.png"
    second = tmp_path / "second.webp"
    Image.new("RGB", (137, 91), "white").save(first)
    Image.new("RGB", (83, 149), "white").save(second)
    expected = [first.read_bytes(), second.read_bytes()]
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"data": [{"b64_json": base64.b64encode(b"edited-bytes").decode("ascii")}]}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    client._urlopen = fake_urlopen
    output = tmp_path / "edited.png"

    result = client.edit_image("preserve both originals", [first, second], output)

    assert result.model == "gpt-image-2"
    assert output.read_bytes() == b"edited-bytes"
    request = requests[0][0]
    assert request.full_url == "https://lingsuan.org/v1/images/edits"
    assert request.get_header("Content-type").startswith("multipart/form-data; boundary=")
    assert request.data.count(b'name="image[]"') == 2
    assert all(raw in request.data for raw in expected)
    assert b'gpt-image-2' in request.data
    assert result.raw["_request"]["reference_image_mode"] == "original_bytes"


def test_provider_connection_test_routes_image_only_provider_to_image_generation() -> None:
    provider = _image_provider()
    calls = []

    def fake_generate(self, prompt, output, *, model=None, size=None, timeout=240):
        calls.append({"prompt": prompt, "model": model, "size": size, "timeout": timeout})
        output.write_bytes(b"png")
        return ImageGenerationResult(provider.name, str(model), output, {})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.PlatformHandler)
    httpd.daemon_threads = True
    serving = threading.Thread(target=httpd.serve_forever, daemon=True)
    serving.start()
    host, port = httpd.server_address
    try:
        with patch.object(server_module, "get_provider", return_value=provider), patch.object(
            OpenAICompatibleClient, "generate_image", fake_generate
        ):
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request(
                "POST",
                "/api/provider-test",
                body=json.dumps({"provider": "lingsuan_image", "model": "gpt-image-2"}),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        serving.join(2)

    assert response.status == 200
    assert payload == {
        "ok": True,
        "provider": "lingsuan_image",
        "model": "gpt-image-2",
        "capability": "image_generation",
    }
    assert calls[0]["model"] == "gpt-image-2"
    assert calls[0]["size"] == "1024x1024"
