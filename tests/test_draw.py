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


if __name__ == "__main__":
    unittest.main()
