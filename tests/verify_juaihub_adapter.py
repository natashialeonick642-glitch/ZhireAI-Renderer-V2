from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import io
import json
import sys
import tempfile
import types
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "zhireAI" / "zz_juaihub_adapter.pyp"


class ApiError(RuntimeError):
    pass


class ImageApiClient:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.settings = {
            "base_url": "https://api.juaihub.cn",
            "api_key": "test-only",
            "auth_type": "bearer",
            "timeout_seconds": 60,
        }

    def generate(self, job, progress=None, should_cancel=None):
        raise AssertionError("Original generate path should not be used")

    def _save_b64(self, value, index, job):
        self.saved_extension = job.get("output_extension")
        return str(self.output_dir / "result.png")

    def _download(self, value, index, should_cancel, job):
        raise AssertionError("URL download was not expected")


def install_fake_api() -> types.ModuleType:
    package = types.ModuleType("lumastage")
    api = types.ModuleType("lumastage.api")
    api.ApiError = ApiError
    api.ImageApiClient = ImageApiClient
    api._check_cancel = lambda callback: (
        (_ for _ in ()).throw(ApiError("cancelled"))
        if callback is not None and callback()
        else None
    )
    api.referenced_indices = lambda prompt: []
    api.composition_locked_prompt = lambda prompt, preserve, context: prompt
    package.api = api
    sys.modules["lumastage"] = package
    sys.modules["lumastage.api"] = api
    return api


def load_adapter():
    install_fake_api()
    loader = importlib.machinery.SourceFileLoader("_adapter_test", str(ADAPTER_PATH))
    spec = importlib.util.spec_from_loader("_adapter_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeResponse:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        if not self.payload:
            return b""
        if size < 0:
            size = len(self.payload)
        chunk, self.payload = self.payload[:size], self.payload[size:]
        return chunk


def main() -> None:
    adapter = load_adapter()
    with tempfile.TemporaryDirectory() as temporary:
        output_dir = Path(temporary)
        client = ImageApiClient(output_dir)
        original_urlopen = adapter.urllib.request.urlopen
        try:
            adapter.urllib.request.urlopen = lambda *args, **kwargs: FakeResponse(
                json.dumps({"ok": True}).encode("utf-8")
            )
            result = adapter._request_json(
                client,
                "/v1beta/models/gemini-3-pro-image-preview:generateContent",
                b"{}",
                "application/json",
                "gemini-3-pro-image-preview",
            )
            assert result == {"ok": True}

            captured = {}
            tested_request_json = adapter._request_json

            def capture_request(client, endpoint, body, content_type, model="", should_cancel=None):
                captured.update(
                    endpoint=endpoint,
                    payload=json.loads(body.decode("utf-8")),
                    model=model,
                )
                return {"candidates": []}

            adapter._request_json = capture_request
            image_path = output_dir / "input.png"
            image_path.write_bytes(b"test-image")
            adapter._gemini_request(
                client,
                "gemini-3-pro-image-preview",
                "test prompt",
                [str(image_path)],
                1536,
                2048,
                "2K",
            )
            payload = captured["payload"]
            config = payload["generationConfig"]
            assert config["responseModalities"] == ["TEXT", "IMAGE"]
            assert config["imageConfig"] == {"aspectRatio": "3:4", "imageSize": "2K"}
            assert captured["model"] == "gemini-3-pro-image-preview"
            inline = payload["contents"][0]["parts"][1]["inlineData"]
            assert base64.b64decode(inline["data"]) == b"test-image"

            adapter._gemini_request = lambda *args, **kwargs: {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(b"\xff\xd8\xffresult-image").decode("ascii"),
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
            generated = adapter._generate_juaihub(
                client,
                {
                    "workflow": "image",
                    "model": "gemini-3-pro-image-preview",
                    "prompt": "test prompt",
                    "scene_image": str(image_path),
                    "references": [],
                    "scene_context": {
                        "depth_image": str(image_path),
                        "preserve_composition": True,
                    },
                    "width": 1536,
                    "height": 2048,
                    "quality": "2K",
                    "count": 1,
                },
                None,
                None,
            )
            assert len(generated) == 1
            generated_path = Path(generated[0])
            assert generated_path.suffix == ".jpg"
            assert generated_path.read_bytes() == b"\xff\xd8\xffresult-image"

            adapter._request_json = tested_request_json
            error = urllib.error.HTTPError(
                "https://api.juaihub.cn/test",
                504,
                "Gateway Timeout",
                {},
                io.BytesIO(b"gateway timeout"),
            )
            try:
                original = adapter.urllib.request.urlopen
                adapter.urllib.request.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(error)
                adapter._request_json(
                    client,
                    "/test",
                    b"{}",
                    "application/json",
                    "gemini-3-pro-image-preview",
                )
                raise AssertionError("Expected a gateway error")
            except ApiError as exc:
                assert "网关未在规定时间内返回结果" in str(exc)
            finally:
                adapter.urllib.request.urlopen = original

            log = (output_dir / "juaihub_adapter_diagnostics.log").read_text(
                encoding="utf-8"
            )
            for stage in (
                "request_started",
                "response_headers",
                "response_complete",
                "payload_ready",
                "http_error",
            ):
                assert "stage={}".format(stage) in log
            assert "test-only" not in log
        finally:
            adapter.urllib.request.urlopen = original_urlopen
    print("JUAIHUB_ADAPTER_CHECK_OK")


if __name__ == "__main__":
    main()
