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


def _png_bytes(color_type=6, extra=b""):
    """最小 PNG 字节（真头 + IHDR），color_type 4/6 带透明通道、2/0 不带。"""
    ihdr = bytes([0, 0, 0, 4, 0, 0, 0, 4, 8, color_type, 0, 0, 0])
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + ihdr + extra


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

    def test_local_png_becomes_data_url(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ref.png"
            p.write_bytes(_png_bytes(6))
            out = draw.normalize_refs([str(p)])
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0].startswith("data:image/png;base64,"))
            self.assertEqual(base64.b64decode(out[0].split(",", 1)[1]), _png_bytes(6))

    def test_magic_bytes_win_over_extension(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lying.jpg"          # 扩展名骗人，内容是 PNG
            p.write_bytes(_png_bytes(6))
            out = draw.normalize_refs([str(p)])
            self.assertTrue(out[0].startswith("data:image/png;base64,"))

    def test_jpeg_and_webp_magic(self):
        with tempfile.TemporaryDirectory() as d:
            jpeg = Path(d) / "a.bin"
            jpeg.write_bytes(b"\xff\xd8\xff\xe0" + b"j" * 8)
            webp = Path(d) / "b.bin"
            webp.write_bytes(b"RIFF" + (8).to_bytes(4, "little") + b"WEBP" + b"12345678")
            out = draw.normalize_refs([str(jpeg), str(webp)])
            self.assertTrue(out[0].startswith("data:image/jpeg;base64,"))
            self.assertTrue(out[1].startswith("data:image/webp;base64,"))

    def test_gif_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.gif"
            p.write_bytes(b"GIF89a" + b"\x00" * 8)
            with self.assertRaises(draw.UsageError) as ctx:
                draw.normalize_refs([str(p)])
            self.assertIn("png / jpg / webp", str(ctx.exception))

    def test_missing_file_raises_usage_error(self):
        with self.assertRaises(draw.UsageError) as ctx:
            draw.normalize_refs(["/no/such/file.png"])
        self.assertIn("/no/such/file.png", str(ctx.exception))

    def test_oversize_file_warns_but_passes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "big.png"
            p.write_bytes(_png_bytes(6) + b"\x00" * (10 * 1024 * 1024))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                out = draw.normalize_refs([str(p)])
            self.assertTrue(out[0].startswith("data:image/png;base64,"))
            self.assertIn("10MB", err.getvalue())

    def test_too_many_refs_warns_but_passes(self):
        refs = [f"https://x/{i}.png" for i in range(7)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = draw.normalize_refs(refs)
        self.assertEqual(len(out), 7)
        self.assertIn("超过 6 张", err.getvalue())


# ---------------------------------------------------------------------------
# normalize_mask
# ---------------------------------------------------------------------------
class TestNormalizeMask(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(draw.normalize_mask(None, has_refs=True))
        self.assertIsNone(draw.normalize_mask("", has_refs=True))

    def test_requires_refs(self):
        with self.assertRaises(draw.UsageError) as ctx:
            draw.normalize_mask("m.png", has_refs=False)
        self.assertIn("--ref", str(ctx.exception))

    def test_url_and_data_url_pass_through(self):
        self.assertEqual(draw.normalize_mask("https://x/m.png", has_refs=True),
                         "https://x/m.png")
        self.assertEqual(draw.normalize_mask("data:image/png;base64,AA", has_refs=True),
                         "data:image/png;base64,AA")

    def test_non_png_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.png"              # 扩展名骗人，内容是 JPEG
            p.write_bytes(b"\xff\xd8\xff\xe0junk")
            with self.assertRaises(draw.UsageError):
                draw.normalize_mask(str(p), has_refs=True)

    def test_png_without_alpha_warns_but_passes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.png"
            p.write_bytes(_png_bytes(color_type=2))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                out = draw.normalize_mask(str(p), has_refs=True)
            self.assertTrue(out.startswith("data:image/png;base64,"))
            self.assertIn("没有透明区", err.getvalue())

    def test_png_with_alpha_no_warning(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.png"
            p.write_bytes(_png_bytes(color_type=6))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                out = draw.normalize_mask(str(p), has_refs=True)
            self.assertTrue(out.startswith("data:image/png;base64,"))
            self.assertNotIn("警告", err.getvalue())

    def test_missing_file(self):
        with self.assertRaises(draw.UsageError):
            draw.normalize_mask("/no/such.png", has_refs=True)


# ---------------------------------------------------------------------------
# build_body
# ---------------------------------------------------------------------------
class TestBuildBody(unittest.TestCase):
    def test_defaults_omit_optional_fields(self):
        body = draw.build_body(model="m", prompt="p", aspect="1024x1024", refs=[])
        self.assertEqual(body, {"model": "m", "prompt": "p", "aspectRatio": "1024x1024",
                                "shutProgress": False})

    def test_refs_mask_quality_background_included(self):
        body = draw.build_body(model="m", prompt="p", aspect="a", refs=["data:x"],
                               mask="data:image/png;base64,M",
                               quality="medium", background="transparent")
        self.assertEqual(body["urls"], ["data:x"])
        self.assertEqual(body["mask"], "data:image/png;base64,M")
        self.assertEqual(body["quality"], "medium")
        self.assertEqual(body["background"], "transparent")

    def test_empty_strings_omitted(self):
        body = draw.build_body(model="m", prompt="p", aspect="a", refs=[],
                               mask="", quality="", background="")
        self.assertNotIn("mask", body)
        self.assertNotIn("quality", body)
        self.assertNotIn("background", body)


# ---------------------------------------------------------------------------
# check_aspect（尺寸软检查）
# ---------------------------------------------------------------------------
class TestCheckAspect(unittest.TestCase):
    def test_auto_passes(self):
        self.assertEqual(draw.check_aspect("gpt-image-2", "auto"), [])
        self.assertEqual(draw.check_aspect("gpt-image-2-vip", "AUTO"), [])

    def test_1k_presets_and_ratios_pass(self):
        for ratio, pixels in draw.SIZES_1K:
            self.assertEqual(draw.check_aspect("gpt-image-2", ratio), [], ratio)
            self.assertEqual(draw.check_aspect("gpt-image-2", pixels), [], pixels)
            self.assertEqual(draw.check_aspect("gpt-image-2.5", ratio), [], ratio)

    def test_1k_non_preset_warns(self):
        warns = draw.check_aspect("gpt-image-2", "1234x5678")
        self.assertEqual(len(warns), 1)
        self.assertIn("1K 预设", warns[0])

    def test_hd_presets_all_pass_four_constraints(self):
        total = 0
        for _ratio, pixels in draw.SIZES_HD:
            for p in pixels:
                total += 1
                for model in ("gpt-image-2-vip", "gpt-image-2.5-flare",
                              "gpt-image-2.5-sunburst"):
                    self.assertEqual(draw.check_aspect(model, p), [], f"{model} {p}")
        self.assertEqual(total, 43)

    def test_hd_rejects_ratio_writing(self):
        warns = draw.check_aspect("gpt-image-2-vip", "16:9")
        self.assertEqual(len(warns), 1)
        self.assertIn("像素值", warns[0])

    def test_hd_constraint_violations(self):
        w = draw.check_aspect("gpt-image-2-vip", "3856x3856")   # 最长边超限
        self.assertTrue(any("3840" in x for x in w))
        w = draw.check_aspect("gpt-image-2-vip", "1023x1024")   # 非 16 倍数
        self.assertEqual(len(w), 1)
        self.assertIn("16 的倍数", w[0])
        w = draw.check_aspect("gpt-image-2-vip", "3840x1024")   # 长短边比 > 3:1
        self.assertEqual(len(w), 1)
        self.assertIn("3:1", w[0])
        w = draw.check_aspect("gpt-image-2-vip", "1024x512")    # 总像素过小
        self.assertEqual(len(w), 1)
        self.assertIn("总像素", w[0])
        w = draw.check_aspect("gpt-image-2-vip", "3840x2176")   # 总像素过大
        self.assertEqual(len(w), 1)
        self.assertIn("总像素", w[0])
        w = draw.check_aspect("gpt-image-2-vip", "abc")         # 认不出
        self.assertEqual(len(w), 1)
        self.assertIn("像素值", w[0])

    def test_unknown_model_not_checked(self):
        self.assertEqual(draw.check_aspect("nano-banana-pro", "16:9"), [])
        self.assertEqual(draw.check_aspect("nano-banana-pro", "3840x2160"), [])


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

    def test_includes_refs_mask_quality_background_in_body(self):
        captured = {}

        def fake_stream(url, api_key, body):
            captured["body"] = body
            yield 'data: {"id":"x","status":"succeeded","results":[{"url":"u"}]}'

        draw.generate("a cat", base="https://api.example.com/", api_key="sk",
                      model="m", aspect="1024x1024", refs=["https://img/1.png"],
                      mask="data:image/png;base64,M",
                      quality="medium", background="transparent",
                      open_stream=fake_stream, log=lambda m: None)
        self.assertEqual(captured["body"]["urls"], ["https://img/1.png"])
        self.assertEqual(captured["body"]["mask"], "data:image/png;base64,M")
        self.assertEqual(captured["body"]["quality"], "medium")
        self.assertEqual(captured["body"]["background"], "transparent")

    def test_failed_carries_reason_and_detail(self):
        def fake_stream(url, api_key, body):
            yield 'data: {"id":"x","status":"failed","failure_reason":"error","error":"boom"}'

        with self.assertRaises(draw.DrawError) as ctx:
            draw.generate("x", base="b", api_key="k", model="m", aspect="a",
                          refs=[], open_stream=fake_stream, log=lambda m: None)
        self.assertEqual(ctx.exception.reason, "error")
        self.assertIn("boom", str(ctx.exception))

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
    def test_prints_catalog_and_size_tables_offline_without_key(self):
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
        self.assertNotIn("积分", out)       # 价格不进目录
        self.assertNotIn("维护中", out)      # 上下架状态不进目录
        self.assertIn("1672x941", out)      # 1K 预设表
        self.assertIn("3840x2160", out)     # HD 预设表
        self.assertIn("快照 2026-10-06", out)
        self.assertIn(draw.MODELS_PAGE, out)


# ---------------------------------------------------------------------------
# main：基础 + 失败重试 + 遮罩
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


class TestFailureHandling(unittest.TestCase):
    def _run(self, side_effects):
        """side_effects：每次 generate 调用的返回值/异常，一个元素管一次调用（末元素复用）。"""
        calls = {"n": 0}
        with tempfile.TemporaryDirectory() as d, clean_env(IMAGE_API_KEY="sk"):
            def fake_generate(prompt, **kw):
                calls["n"] += 1
                eff = side_effects[min(calls["n"] - 1, len(side_effects) - 1)]
                if isinstance(eff, Exception):
                    raise eff
                return ("t", ["https://img/a.png"])

            orig_gen, orig_dl = draw.generate, draw.download
            draw.generate = fake_generate
            draw.download = lambda url, out, tid, idx, **kw: "/abs/x.png"
            err = io.StringIO()
            try:
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(err):
                    rc = draw.main(["a cat"])
            finally:
                draw.generate, draw.download = orig_gen, orig_dl
        return rc, calls["n"], err.getvalue()

    def test_error_retries_once_then_succeeds(self):
        rc, n, err = self._run([draw.DrawError("生成失败: error", reason="error"), None])
        self.assertEqual((rc, n), (0, 2))
        self.assertIn("自动重试 1 次", err)

    def test_error_twice_fails(self):
        rc, n, _ = self._run([draw.DrawError("生成失败: error", reason="error")])
        self.assertEqual((rc, n), (1, 2))

    def test_input_moderation_no_retry(self):
        rc, n, err = self._run([draw.DrawError("生成失败: input_moderation（bad）",
                                               reason="input_moderation")])
        self.assertEqual((rc, n), (1, 1))
        self.assertIn("违规", err)

    def test_output_moderation_no_retry(self):
        rc, n, err = self._run([draw.DrawError("生成失败: output_moderation",
                                               reason="output_moderation")])
        self.assertEqual((rc, n), (1, 1))
        self.assertIn("违规", err)


class TestMainMask(unittest.TestCase):
    def test_mask_requires_ref(self):
        with tempfile.TemporaryDirectory() as d, clean_env(IMAGE_API_KEY="sk"):
            m = Path(d) / "m.png"
            m.write_bytes(_png_bytes(6))
            with contextlib.redirect_stderr(io.StringIO()):
                rc = draw.main(["a cat", "--mask", str(m)])
        self.assertEqual(rc, 2)

    def test_mask_passed_to_generate(self):
        captured = {}
        with tempfile.TemporaryDirectory() as d, clean_env(IMAGE_API_KEY="sk"):
            ref = Path(d) / "r.png"
            ref.write_bytes(_png_bytes(6))
            mask = Path(d) / "m.png"
            mask.write_bytes(_png_bytes(6))
            orig_gen, orig_dl = draw.generate, draw.download

            def fake_generate(prompt, **kw):
                captured.update(kw)
                return ("t", ["https://img/a.png"])

            draw.generate = fake_generate
            draw.download = lambda url, out, tid, idx, **kw: "/abs/x.png"
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = draw.main(["a cat", "--ref", str(ref), "--mask", str(mask)])
            finally:
                draw.generate, draw.download = orig_gen, orig_dl
        self.assertEqual(rc, 0)
        self.assertTrue(captured["mask"].startswith("data:image/png;base64,"))
        self.assertEqual(len(captured["refs"]), 1)


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
