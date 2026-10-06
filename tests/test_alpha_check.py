import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import alpha_check

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ---------------------------------------------------------------------------
# analyze：8 类合成图的结论
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_PIL, "需要 Pillow")
class TestAnalyzeVerdicts(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.dir = self.d.name

    def tearDown(self):
        self.d.cleanup()

    def _analyze(self, im, name="t.png"):
        p = Path(self.dir) / name
        im.save(p)
        return alpha_check.analyze(p)

    def test_centered_sticker_is_truly_transparent(self):
        im = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((30, 30, 50, 50), fill=(255, 0, 0, 255))
        self.assertIn("结论：真透明", self._analyze(im)[-1])

    def test_bottom_hugging_subject_is_truly_transparent(self):
        im = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle((20, 40, 60, 79), fill=(255, 0, 0, 255))
        self.assertIn("结论：真透明", self._analyze(im)[-1])

    def test_bottom_half_alpha_253_is_truly_transparent(self):
        im = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle((0, 40, 79, 79), fill=(10, 20, 30, 253))
        lines = self._analyze(im)
        self.assertIn("结论：真透明", lines[-1])
        self.assertIn("半透明 0.0%", lines[1])   # alpha=253 计入「不透明」，不算半透明
        self.assertIn("不透明 50.0%", lines[1])

    def test_suspect_border_ratio(self):
        im = Image.new("RGBA", (80, 80), (200, 200, 200, 255))
        ImageDraw.Draw(im).rectangle((30, 0, 41, 3), fill=(0, 0, 0, 0))  # 顶部带挖 ~3.9%
        self.assertIn("存疑", self._analyze(im)[-1])

    def test_white_rgb_no_channel(self):
        im = Image.new("RGB", (80, 80), (255, 255, 255))
        last = self._analyze(im)[-1]
        self.assertIn("不是真透明", last)
        self.assertIn("没有透明通道", last)
        self.assertIn("白或浅色底", last)

    def test_white_rgba_has_channel(self):
        im = Image.new("RGBA", (80, 80), (255, 255, 255, 255))
        last = self._analyze(im)[-1]
        self.assertIn("不是真透明", last)
        self.assertIn("有通道但边框不透明", last)

    def test_checkerboard_painted_in(self):
        im = Image.new("RGBA", (80, 80))
        px = im.load()
        for y in range(80):
            for x in range(80):
                v = 255 if (x // 16 + y // 16) % 2 == 0 else 230
                px[x, y] = (v, v, v, 255)
        last = self._analyze(im)[-1]
        self.assertIn("不是真透明", last)
        self.assertIn("疑似棋盘格", last)

    def test_dark_scene_is_other_base(self):
        im = Image.new("RGBA", (80, 80), (27, 29, 60, 255))
        last = self._analyze(im)[-1]
        self.assertIn("不是真透明", last)
        self.assertIn("其它底", last)

    def test_alpha_253_counts_as_opaque(self):
        im = Image.new("RGBA", (80, 80), (0, 0, 0, 253))
        self.assertIn("不透明 100.0%", self._analyze(im)[1])

    def test_report_head_lines(self):
        im = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        lines = self._analyze(im, name="sticker.png")
        self.assertIn("sticker.png: 80x80 mode=RGBA", lines[0])
        self.assertIn("边框全透明占比", lines[2])


# ---------------------------------------------------------------------------
# preview：--bg 底色预览
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
# 源码：只用 Pillow，不引入 numpy
# ---------------------------------------------------------------------------
class TestNoNumpy(unittest.TestCase):
    def test_source_does_not_import_numpy(self):
        src = Path(alpha_check.__file__).read_text()
        self.assertNotIn("import numpy", src)


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
