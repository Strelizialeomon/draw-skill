import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error

import draw


# ---------------------------------------------------------------------------
# _parse_sse_event
# ---------------------------------------------------------------------------
class TestParseSseEvent(unittest.TestCase):
    def test_parses_data_line(self):
        ev = draw._parse_sse_event('data: {"status":"running","progress":5}')
        self.assertEqual(ev, {"status": "running", "progress": 5})

    def test_non_data_line_returns_none(self):
        self.assertIsNone(draw._parse_sse_event(""))
        self.assertIsNone(draw._parse_sse_event(":keepalive"))
        self.assertIsNone(draw._parse_sse_event("event: ping"))

    def test_empty_payload_returns_none(self):
        self.assertIsNone(draw._parse_sse_event("data: "))


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------
class TestGenerate(unittest.TestCase):
    def test_streams_progress_then_returns_id_and_urls(self):
        captured = {}
        logs = []

        def fake_stream(url, api_key, body):
            captured["url"] = url
            captured["api_key"] = api_key
            captured["body"] = body
            yield 'data: {"id":"task-9","status":"running","progress":30,"results":null}'
            yield ''
            yield ('data: {"id":"task-9","status":"succeeded","progress":100,'
                   '"results":[{"url":"https://img/a.png"},{"url":"https://img/b.png"}]}')

        task_id, urls = draw.generate(
            "a cat",
            base="https://api.example.com",
            api_key="sk",
            model="gpt-image-2",
            aspect="1024x1024",
            refs=[],
            open_stream=fake_stream,
            log=logs.append,
        )

        self.assertEqual(task_id, "task-9")
        self.assertEqual(urls, ["https://img/a.png", "https://img/b.png"])
        self.assertEqual(captured["url"], "https://api.example.com/v1/draw/completions")
        self.assertEqual(captured["api_key"], "sk")
        self.assertEqual(captured["body"]["shutProgress"], False)
        self.assertNotIn("urls", captured["body"])
        self.assertTrue(any("30" in m for m in logs))

    def test_includes_refs_in_body(self):
        captured = {}

        def fake_stream(url, api_key, body):
            captured["body"] = body
            yield 'data: {"id":"x","status":"succeeded","results":[{"url":"u"}]}'

        draw.generate("a cat", base="https://api.example.com/", api_key="sk",
                      model="m", aspect="1024x1024", refs=["https://img/1.png"],
                      open_stream=fake_stream, log=lambda m: None)
        self.assertEqual(captured["body"]["urls"], ["https://img/1.png"])

    def test_raises_on_failed(self):
        def fake_stream(url, api_key, body):
            yield 'data: {"id":"x","status":"failed","failure_reason":"nsfw","error":""}'

        with self.assertRaises(draw.DrawError) as ctx:
            draw.generate("x", base="b", api_key="k", model="m", aspect="a",
                          refs=[], open_stream=fake_stream, log=lambda m: None)
        self.assertIn("nsfw", str(ctx.exception))

    def test_raises_when_succeeded_but_no_results(self):
        def fake_stream(url, api_key, body):
            yield 'data: {"id":"x","status":"succeeded","results":[]}'

        with self.assertRaises(draw.DrawError):
            draw.generate("x", base="b", api_key="k", model="m", aspect="a",
                          refs=[], open_stream=fake_stream, log=lambda m: None)

    def test_raises_when_stream_ends_without_terminal(self):
        def fake_stream(url, api_key, body):
            yield 'data: {"id":"x","status":"running","progress":50}'

        with self.assertRaises(draw.DrawError):
            draw.generate("x", base="b", api_key="k", model="m", aspect="a",
                          refs=[], open_stream=fake_stream, log=lambda m: None)


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------
class TestDownload(unittest.TestCase):
    def test_writes_file_and_returns_abspath(self):
        with tempfile.TemporaryDirectory() as d:
            def fake_fetch(url):
                self.assertEqual(url, "https://img/a.png")
                return b"PNGDATA"

            path = draw.download("https://img/a.png", d, "task-123", 1, fetch=fake_fetch)

            self.assertTrue(os.path.isabs(path))
            self.assertEqual(os.path.basename(path), "draw-task-123-1.png")
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"PNGDATA")

    def test_creates_missing_out_dir(self):
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, "imgs")
            path = draw.download("https://img/a.png", nested, "t", 2, fetch=lambda u: b"x")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(os.path.basename(path), "draw-t-2.png")

    def test_extension_taken_from_url(self):
        with tempfile.TemporaryDirectory() as d:
            path = draw.download("https://img/a.webp", d, "t", 1, fetch=lambda u: b"x")
            self.assertEqual(os.path.basename(path), "draw-t-1.webp")


# ---------------------------------------------------------------------------
# _http_post_stream
# ---------------------------------------------------------------------------
class _FakeStreamResp:
    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestHttpPostStream(unittest.TestCase):
    def test_yields_decoded_lines_with_bearer(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["auth"] = req.get_header("Authorization")
            captured["method"] = req.get_method()
            captured["data"] = req.data
            return _FakeStreamResp([b"data: {\"a\":1}\n", b"\n"])

        orig = draw.urllib.request.urlopen
        draw.urllib.request.urlopen = fake_urlopen
        try:
            lines = list(draw._http_post_stream("https://api.example.com/v1/draw/completions",
                                                "sk-key", {"prompt": "x"}, timeout=5))
        finally:
            draw.urllib.request.urlopen = orig

        self.assertEqual(lines, ['data: {"a":1}\n', "\n"])
        self.assertEqual(captured["auth"], "Bearer sk-key")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(json.loads(captured["data"].decode()), {"prompt": "x"})

    def test_maps_httperror_to_drawerror(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b"no access"))

        orig = draw.urllib.request.urlopen
        draw.urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(draw.DrawError) as ctx:
                list(draw._http_post_stream("https://api.example.com/x", "k", {}, timeout=5))
        finally:
            draw.urllib.request.urlopen = orig
        self.assertIn("401", str(ctx.exception))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
class TestMain(unittest.TestCase):
    def test_missing_env_returns_2(self):
        orig = dict(os.environ)
        os.environ.pop("IMAGE_API_KEY", None)
        os.environ.pop("IMAGE_API_BASE", None)
        try:
            rc = draw.main(["a cat"])
        finally:
            os.environ.clear()
            os.environ.update(orig)
        self.assertEqual(rc, 2)

    def test_happy_path_prints_paths_and_returns_0(self):
        orig = dict(os.environ)
        os.environ["IMAGE_API_KEY"] = "sk"
        os.environ["IMAGE_API_BASE"] = "https://api.example.com"
        orig_gen, orig_dl = draw.generate, draw.download
        draw.generate = lambda prompt, **kw: ("task-9", ["https://img/a.png"])
        draw.download = lambda url, out, tid, idx, **kw: f"/abs/draw-{tid}-{idx}.png"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = draw.main(["a cat", "--out", "."])
        finally:
            draw.generate, draw.download = orig_gen, orig_dl
            os.environ.clear()
            os.environ.update(orig)
        self.assertEqual(rc, 0)
        self.assertIn("/abs/draw-task-9-1.png", buf.getvalue())

    def test_url_only_prints_urls(self):
        orig = dict(os.environ)
        os.environ["IMAGE_API_KEY"] = "sk"
        os.environ["IMAGE_API_BASE"] = "https://api.example.com"
        orig_gen = draw.generate
        draw.generate = lambda prompt, **kw: ("t", ["https://img/x.png"])
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = draw.main(["a cat", "--url-only"])
        finally:
            draw.generate = orig_gen
            os.environ.clear()
            os.environ.update(orig)
        self.assertEqual(rc, 0)
        self.assertIn("https://img/x.png", buf.getvalue())


# ---------------------------------------------------------------------------
# model selection precedence
# ---------------------------------------------------------------------------
class TestModelSelection(unittest.TestCase):
    def _captured_model(self, argv, env):
        orig = dict(os.environ)
        os.environ["IMAGE_API_KEY"] = "sk"
        os.environ["IMAGE_API_BASE"] = "https://api.example.com"
        os.environ.pop("IMAGE_MODEL", None)
        for k, v in env.items():
            os.environ[k] = v
        captured = {}

        def fake_generate(prompt, **kw):
            captured["model"] = kw.get("model")
            return ("t", ["https://img/a.png"])

        orig_gen, orig_dl = draw.generate, draw.download
        draw.generate = fake_generate
        draw.download = lambda url, out, tid, idx, **kw: "/abs/x.png"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                draw.main(argv)
        finally:
            draw.generate, draw.download = orig_gen, orig_dl
            os.environ.clear()
            os.environ.update(orig)
        return captured["model"]

    def test_defaults_to_gpt_image_2(self):
        self.assertEqual(self._captured_model(["a cat"], {}), "gpt-image-2")

    def test_env_var_is_used_when_no_flag(self):
        self.assertEqual(self._captured_model(["a cat"], {"IMAGE_MODEL": "seedream"}), "seedream")

    def test_flag_overrides_env_var(self):
        self.assertEqual(
            self._captured_model(["a cat", "--model", "flux"], {"IMAGE_MODEL": "seedream"}),
            "flux",
        )


if __name__ == "__main__":
    unittest.main()
