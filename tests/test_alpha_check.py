import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import alpha_check

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_PIL, "需要 Pillow")
class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.dir = Path(self.d.name)

    def tearDown(self):
        self.d.cleanup()

    def _save(self, name, im):
        p = self.dir / name
        im.save(p)
        return p

    def test_truly_transparent(self):
        p = self._save("t.png", Image.new("RGBA", (80, 80), (255, 0, 0, 0)))
        lines = alpha_check.analyze(p)
        self.assertIn("真透明", lines[-1])
        self.assertTrue(lines[-1].startswith("  结论："))
        self.assertIn("t.png", lines[0])

    def test_opaque_rgb_is_not_transparent(self):
        p = self._save("white.png", Image.new("RGB", (80, 80), (255, 255, 255)))
        self.assertIn("不是真透明", alpha_check.analyze(p)[-1])

    def test_semi_transparent_is_not_transparent(self):
        p = self._save("semi.png", Image.new("RGBA", (80, 80), (0, 0, 0, 128)))
        lines = alpha_check.analyze(p)
        self.assertIn("不是真透明", lines[-1])
        self.assertIn("半透明 100.0%", lines[1])

    def test_opaque_bottom_mid_is_not_transparent(self):
        im = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        for x in range(24, 56):
            for y in range(56, 80):
                im.putpixel((x, y), (255, 0, 0, 255))
        p = self._save("bm.png", im)
        self.assertIn("不是真透明", alpha_check.analyze(p)[-1])


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_PIL, "需要 Pillow")
class TestPreview(unittest.TestCase):
    def test_preview_written_next_to_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.png"
            Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(p)
            out = alpha_check.preview(p, "1b1d3c")
            self.assertTrue(out.exists())
            self.assertEqual(out.name, "x_on_1b1d3c.jpg")
            self.assertEqual(out.parent, p.parent)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_PIL, "需要 Pillow")
class TestMain(unittest.TestCase):
    def _make(self, d, name="t.png"):
        p = Path(d) / name
        Image.new("RGBA", (80, 80), (0, 0, 0, 0)).save(p)
        return p

    def test_report_rc0(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._make(d)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = alpha_check.main([str(p)])
            self.assertEqual(rc, 0)
            self.assertIn("真透明", buf.getvalue())

    def test_missing_file_rc2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = alpha_check.main(["/no/such/img.png"])
        self.assertEqual(rc, 2)
        self.assertIn("/no/such/img.png", err.getvalue())

    def test_bad_bg_rc2(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._make(d)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = alpha_check.main([str(p), "--bg", "zzz"])
        self.assertEqual(rc, 2)
        self.assertIn("--bg", err.getvalue())

    def test_bg_generates_preview(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._make(d)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = alpha_check.main([str(p), "--bg", "#1B1D3C"])
            self.assertEqual(rc, 0)
            self.assertIn("预览", buf.getvalue())
            self.assertTrue((Path(d) / "t_on_1b1d3c.jpg").exists())


if __name__ == "__main__":
    unittest.main()
