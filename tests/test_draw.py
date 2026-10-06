import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import draw


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
_ENV_KEYS = ("IMAGE_API_KEY", "GRSAI_KEY", "IMAGE_API_BASE", "IMAGE_MODEL")


@contextlib.contextmanager
def clean_env(**overrides):
    """先清掉四个相关环境变量再跑，退出时还原。"""
    orig = dict(os.environ)
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(overrides)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(orig)


@contextlib.contextmanager
def key_file(path):
    """把 draw.KEY_FILE 指到别处，退出时还原。"""
    orig = draw.KEY_FILE
    draw.KEY_FILE = Path(path)
    try:
        yield
    finally:
        draw.KEY_FILE = orig


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
# resolve_key / resolve_base
# ---------------------------------------------------------------------------
class TestResolveKey(unittest.TestCase):
    def test_env_image_api_key_wins(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "key"
            f.write_text("sk-from-file")
            with clean_env(IMAGE_API_KEY="sk-from-env"), key_file(f):
                self.assertEqual(draw.resolve_key(), "sk-from-env")

    def test_gr_sai_key_is_second(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "key"
            f.write_text("sk-from-file")
            with clean_env(GRSAI_KEY="sk-grsai"), key_file(f):
                self.assertEqual(draw.resolve_key(), "sk-grsai")

    def test_file_fallback_strips_whitespace(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "key"
            f.write_text("  sk-from-file\n")
            with clean_env(), key_file(f):
                self.assertEqual(draw.resolve_key(), "sk-from-file")

    def test_all_missing_raises_usage_error(self):
        with tempfile.TemporaryDirectory() as d:
            with clean_env(), key_file(Path(d) / "nope"):
                with self.assertRaises(draw.UsageError):
                    draw.resolve_key()


class TestResolveBase(unittest.TestCase):
    def test_env_overrides_default(self):
        with clean_env(IMAGE_API_BASE="https://x.example.com"):
            self.assertEqual(draw.resolve_base(), "https://x.example.com")

    def test_default_is_grsai_host(self):
        with clean_env():
            self.assertEqual(draw.resolve_base(), draw.DEFAULT_BASE)
        self.assertEqual(draw.DEFAULT_BASE, "https://grsai.dakka.com.cn")


# ---------------------------------------------------------------------------
# normalize_refs
# ---------------------------------------------------------------------------
class TestNormalizeRefs(unittest.TestCase):
    def test_urls_pass_through(self):
        refs = ["http://a/1.png", "https://b/2.jpg", "data:image/png;base64,AAA"]
        self.assertEqual(draw.normalize_refs(refs), refs)

    def test_local_file_becomes_data_url(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ref.png"
            p.write_bytes(b"\x89PNG123")
            out = draw.normalize_refs([str(p)])
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0].startswith("data:image/png;base64,"))
            self.assertEqual(base64.b64decode(out[0].split(",", 1)[1]), b"\x89PNG123")

    def test_mime_by_extension_with_png_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d) / "a.jpg"
            j.write_bytes(b"jpg-bytes")
            weird = Path(d) / "b.xyz"
            weird.write_bytes(b"weird")
            out = draw.normalize_refs([str(j), str(weird)])
            self.assertTrue(out[0].startswith("data:image/jpeg;base64,"))
            self.assertTrue(out[1].startswith("data:image/png;base64,"))

    def test_missing_file_raises_usage_error(self):
        with self.assertRaises(draw.UsageError) as ctx:
            draw.normalize_refs(["/no/such/file.png"])
        self.assertIn("/no/such/file.png", str(ctx.exception))


# ---------------------------------------------------------------------------
# build_body
# ---------------------------------------------------------------------------
class TestBuildBody(unittest.TestCase):
    def test_defaults_omit_optional_fields(self):
        body = draw.build_body(model="m", prompt="p", aspect="1024x1024", refs=[])
        self.assertEqual(body, {"model": "m", "prompt": "p", "aspectRatio": "1024x1024",
                                "shutProgress": False})

    def test_refs_quality_background_included(self):
        body = draw.build_body(model="m", prompt="p", aspect="a", refs=["data:x"],
                               quality="medium", background="transparent")
        self.assertEqual(body["urls"], ["data:x"])
        self.assertEqual(body["quality"], "medium")
        self.assertEqual(body["background"], "transparent")

    def test_empty_strings_omitted(self):
        body = draw.build_body(model="m", prompt="p", aspect="a", refs=[],
                               quality="", background="")
        self.assertNotIn("quality", body)
        self.assertNotIn("background", body)


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

    def test_passes_quality_and_background(self):
        captured = {}

        def fake_stream(url, api_key, body):
            captured["body"] = body
            yield 'data: {"id":"x","status":"succeeded","results":[{"url":"u"}]}'

        draw.generate("a cat", base="b", api_key="k", model="m", aspect="a", refs=[],
                      quality="medium", background="transparent",
                      open_stream=fake_stream, log=lambda m: None)
        self.assertEqual(captured["body"]["quality"], "medium")
        self.assertEqual(captured["body"]["background"], "transparent")

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
# _inspect_files
# ---------------------------------------------------------------------------
class TestInspectFiles(unittest.TestCase):
    def test_module_missing_skips(self):
        err = io.StringIO()
        with mock.patch.dict(sys.modules, {"alpha_check": None}):
            with contextlib.redirect_stderr(err):
                draw._inspect_files(["/tmp/x.png"])
        self.assertIn("体检跳过", err.getvalue())

    def test_pillow_missing_skips(self):
        fake = types.ModuleType("alpha_check")
        fake.HAS_PIL = False
        err = io.StringIO()
        with mock.patch.dict(sys.modules, {"alpha_check": fake}):
            with contextlib.redirect_stderr(err):
                draw._inspect_files(["/tmp/x.png"])
        self.assertIn("体检跳过", err.getvalue())
        self.assertIn("Pillow", err.getvalue())

    def test_reports_lines_to_stderr(self):
        fake = types.ModuleType("alpha_check")
        fake.HAS_PIL = True
        fake.analyze = lambda p: [f"{p}: 40x40", "  结论：真透明"]
        err = io.StringIO()
        with mock.patch.dict(sys.modules, {"alpha_check": fake}):
            with contextlib.redirect_stderr(err):
                draw._inspect_files(["/tmp/x.png"])
        self.assertIn("结论：真透明", err.getvalue())


# ---------------------------------------------------------------------------
# --list-models
# ---------------------------------------------------------------------------
class TestListModels(unittest.TestCase):
    def test_prints_catalog_offline_without_key(self):
        calls = []

        def fake_urlopen(*a, **k):
            calls.append(a)
            raise AssertionError("--list-models 不该触网")

        orig = draw.urllib.request.urlopen
        draw.urllib.request.urlopen = fake_urlopen
        buf = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as d, clean_env(), key_file(Path(d) / "nope"):
                with contextlib.redirect_stdout(buf):
                    rc = draw.main(["--list-models"])
        finally:
            draw.urllib.request.urlopen = orig

        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        for name in ("gpt-image-2", "gpt-image-2-vip", "gpt-image-2.5",
                     "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
            self.assertIn(name, out)
        self.assertIn("维护中", out)
        self.assertIn("快照 2026-10-06", out)
        self.assertIn(draw.MODELS_PAGE, out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
class TestMain(unittest.TestCase):
    def test_missing_key_returns_2(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d, clean_env(), key_file(Path(d) / "nope"):
            with contextlib.redirect_stderr(err):
                rc = draw.main(["a cat"])
        self.assertEqual(rc, 2)
        self.assertIn("IMAGE_API_KEY", err.getvalue())

    def test_missing_prompt_returns_2(self):
        with clean_env():
            with contextlib.redirect_stderr(io.StringIO()):
                rc = draw.main([])
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


class TestMainInspect(unittest.TestCase):
    def _run_main(self, argv):
        recorded = []
        with tempfile.TemporaryDirectory() as d, clean_env(IMAGE_API_KEY="sk"):
            orig_gen, orig_dl, orig_ins = draw.generate, draw.download, draw._inspect_files
            draw.generate = lambda prompt, **kw: ("t1", ["https://img/a.png"])
            draw.download = lambda url, out, tid, idx, **kw: "/abs/x1.png"
            draw._inspect_files = lambda paths: recorded.append(paths)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = draw.main(argv)
            finally:
                draw.generate, draw.download, draw._inspect_files = orig_gen, orig_dl, orig_ins
        return rc, recorded

    def test_inspect_called_with_downloaded_paths(self):
        rc, recorded = self._run_main(["a cat", "--inspect"])
        self.assertEqual(rc, 0)
        self.assertEqual(recorded, [["/abs/x1.png"]])

    def test_url_only_skips_inspect(self):
        rc, recorded = self._run_main(["a cat", "--url-only", "--inspect"])
        self.assertEqual(rc, 0)
        self.assertEqual(recorded, [])


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
