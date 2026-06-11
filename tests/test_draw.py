import json
import unittest
import draw


class TestSubmit(unittest.TestCase):
    def test_returns_id_and_builds_text_body(self):
        captured = {}

        def fake_post(url, api_key, body):
            captured["url"] = url
            captured["api_key"] = api_key
            captured["body"] = body
            return {"code": 0, "msg": "success", "data": {"id": "task-123"}}

        task_id = draw.submit(
            "a cat",
            base="https://api.example.com",
            api_key="sk-xxx",
            model="gpt-image-2",
            aspect="1024x1024",
            refs=[],
            post=fake_post,
        )

        self.assertEqual(task_id, "task-123")
        self.assertEqual(captured["url"], "https://api.example.com/v1/draw/completions")
        self.assertEqual(captured["api_key"], "sk-xxx")
        self.assertEqual(
            captured["body"],
            {"model": "gpt-image-2", "prompt": "a cat", "aspectRatio": "1024x1024"},
        )
        self.assertNotIn("urls", captured["body"])

    def test_includes_refs_when_present(self):
        captured = {}

        def fake_post(url, api_key, body):
            captured["body"] = body
            return {"code": 0, "data": {"id": "x"}}

        draw.submit(
            "a cat",
            base="https://api.example.com/",
            api_key="sk",
            model="m",
            aspect="1024x1024",
            refs=["https://img/1.png"],
            post=fake_post,
        )
        self.assertEqual(captured["body"]["urls"], ["https://img/1.png"])

    def test_raises_on_nonzero_code(self):
        def fake_post(url, api_key, body):
            return {"code": 1, "msg": "bad key", "data": {}}

        with self.assertRaises(draw.DrawError):
            draw.submit("x", base="b", api_key="k", model="m", aspect="a", refs=[], post=fake_post)

    def test_raises_when_no_id(self):
        def fake_post(url, api_key, body):
            return {"code": 0, "data": {}}

        with self.assertRaises(draw.DrawError):
            draw.submit("x", base="b", api_key="k", model="m", aspect="a", refs=[], post=fake_post)


class TestPollSuccess(unittest.TestCase):
    def test_returns_urls_on_succeeded(self):
        responses = iter([
            {"code": 0, "data": {"status": "running", "progress": 30, "results": []}},
            {"code": 0, "data": {"status": "succeeded", "progress": 100,
                                 "results": [{"url": "https://img/a.png"},
                                             {"url": "https://img/b.png"}]}},
        ])
        calls = {"n": 0}

        def fake_post(url, api_key, body):
            self.assertEqual(url, "https://api.example.com/v1/draw/result")
            self.assertEqual(body, {"id": "task-123"})
            calls["n"] += 1
            return next(responses)

        urls = draw.poll(
            "task-123",
            base="https://api.example.com",
            api_key="sk",
            interval=3,
            timeout=300,
            post=fake_post,
            sleep=lambda s: None,
            now=lambda: 0.0,
            log=lambda m: None,
        )
        self.assertEqual(urls, ["https://img/a.png", "https://img/b.png"])
        self.assertEqual(calls["n"], 2)

    def test_raises_on_nonzero_code(self):
        def fake_post(url, api_key, body):
            return {"code": 1, "msg": "boom"}

        with self.assertRaises(draw.DrawError):
            draw.poll("x", base="b", api_key="k", interval=0, timeout=10,
                      post=fake_post, sleep=lambda s: None, now=lambda: 0.0, log=lambda m: None)

    def test_raises_when_succeeded_but_no_urls(self):
        def fake_post(url, api_key, body):
            return {"code": 0, "data": {"status": "succeeded", "results": []}}

        with self.assertRaises(draw.DrawError):
            draw.poll("x", base="b", api_key="k", interval=0, timeout=10,
                      post=fake_post, sleep=lambda s: None, now=lambda: 0.0, log=lambda m: None)


class TestPollFailAndTimeout(unittest.TestCase):
    def test_raises_on_failed_status_with_reason(self):
        def fake_post(url, api_key, body):
            return {"code": 0, "data": {"status": "failed",
                                        "failure_reason": "nsfw", "error": ""}}

        with self.assertRaises(draw.DrawError) as ctx:
            draw.poll("x", base="b", api_key="k", interval=0, timeout=10,
                      post=fake_post, sleep=lambda s: None, now=lambda: 0.0, log=lambda m: None)
        self.assertIn("nsfw", str(ctx.exception))

    def test_raises_on_timeout(self):
        clock = {"t": 0.0}

        def fake_now():
            return clock["t"]

        def fake_sleep(s):
            clock["t"] += 5.0  # 每次 sleep 推进 5 秒，迅速越过 deadline

        def fake_post(url, api_key, body):
            return {"code": 0, "data": {"status": "running", "progress": 10}}

        with self.assertRaises(draw.DrawError) as ctx:
            draw.poll("x", base="b", api_key="k", interval=3, timeout=10,
                      post=fake_post, sleep=fake_sleep, now=fake_now, log=lambda m: None)
        self.assertIn("仍未完成", str(ctx.exception))


class TestDownload(unittest.TestCase):
    def test_writes_file_and_returns_abspath(self):
        import os
        import tempfile
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
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, "imgs")
            path = draw.download("https://img/a.png", nested, "t", 2, fetch=lambda u: b"x")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(os.path.basename(path), "draw-t-2.png")


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestHttpPost(unittest.TestCase):
    def test_sends_json_with_bearer_and_parses_response(self):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["auth"] = req.get_header("Authorization")
            captured["ctype"] = req.get_header("Content-type")
            captured["data"] = req.data
            return _FakeResp(b'{"code":0,"data":{"id":"abc"}}')

        orig = draw.urllib.request.urlopen
        draw.urllib.request.urlopen = fake_urlopen
        try:
            out = draw._http_post("https://api.example.com/v1/draw/completions",
                                  "sk-key", {"prompt": "x"})
        finally:
            draw.urllib.request.urlopen = orig

        self.assertEqual(out, {"code": 0, "data": {"id": "abc"}})
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["auth"], "Bearer sk-key")
        self.assertEqual(captured["ctype"], "application/json")
        self.assertEqual(json.loads(captured["data"].decode()), {"prompt": "x"})

    def test_maps_httperror_to_drawerror(self):
        import io
        import urllib.error

        def fake_urlopen(req):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b"no access"))

        orig = draw.urllib.request.urlopen
        draw.urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(draw.DrawError) as ctx:
                draw._http_post("https://api.example.com/x", "k", {})
        finally:
            draw.urllib.request.urlopen = orig
        self.assertIn("401", str(ctx.exception))


class TestMain(unittest.TestCase):
    def test_missing_env_returns_2(self):
        import os
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
        import contextlib
        import io
        import os
        orig = dict(os.environ)
        os.environ["IMAGE_API_KEY"] = "sk"
        os.environ["IMAGE_API_BASE"] = "https://api.example.com"
        orig_submit, orig_poll, orig_download = draw.submit, draw.poll, draw.download
        draw.submit = lambda prompt, **kw: "task-9"
        draw.poll = lambda task_id, **kw: ["https://img/a.png"]
        draw.download = lambda url, out, tid, idx, **kw: f"/abs/draw-{tid}-{idx}.png"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = draw.main(["a cat", "--out", "."])
        finally:
            draw.submit, draw.poll, draw.download = orig_submit, orig_poll, orig_download
            os.environ.clear()
            os.environ.update(orig)
        self.assertEqual(rc, 0)
        self.assertIn("/abs/draw-task-9-1.png", buf.getvalue())

    def test_url_only_prints_urls(self):
        import contextlib
        import io
        import os
        orig = dict(os.environ)
        os.environ["IMAGE_API_KEY"] = "sk"
        os.environ["IMAGE_API_BASE"] = "https://api.example.com"
        orig_submit, orig_poll = draw.submit, draw.poll
        draw.submit = lambda prompt, **kw: "t"
        draw.poll = lambda task_id, **kw: ["https://img/x.png"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = draw.main(["a cat", "--url-only"])
        finally:
            draw.submit, draw.poll = orig_submit, orig_poll
            os.environ.clear()
            os.environ.update(orig)
        self.assertEqual(rc, 0)
        self.assertIn("https://img/x.png", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
