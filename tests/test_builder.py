import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from x_article_workbench.builder import BuildError, build


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image-bytes"


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def article(self, text, images=None):
        source = self.root / "article.md"
        source.write_text(text, encoding="utf-8")
        for name in images or []:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(PNG)
        return source

    def test_build_preserves_content_and_orders_images(self):
        source = self.article(
            "# 标题\n\n![封面](cover.png)\n\n开头 **重点** [来源](https://example.com/?a=1&b=2)。\n\n"
            "## 小节\n\n- 项目 A\n- 项目 B\n\n![图](assets/chart.png)\n\n1. 第一步\n2. 第二步\n",
            ["cover.png", "assets/chart.png"],
        )
        output = self.root / "publish"
        result = build(source, output)
        page = (output / "index.html").read_text(encoding="utf-8")
        plain = (output / "正文-纯文字备用.txt").read_text(encoding="utf-8")
        self.assertIn("<strong>重点</strong>", page)
        self.assertIn("https://example.com/?a=1&amp;b=2", page)
        self.assertIn("• 项目 A", plain)
        self.assertIn("1、第一步", plain)
        self.assertIn("【插入图 1】", plain)
        self.assertEqual(result["cover_images"], 1)
        self.assertEqual(result["body_images"], 1)
        self.assertEqual(len(list((output / "images").iterdir())), 2)
        with zipfile.ZipFile(output.with_suffix(".zip")) as archive:
            names = archive.namelist()
        self.assertIn("publish/index.html", names)
        self.assertTrue(any(name.startswith("publish/images/00-cover-") for name in names))

    def test_raw_html_is_text_and_unsafe_scheme_fails(self):
        safe = self.article("# 标题\n\n<script>window.pwned=1</script>\n")
        output = self.root / "safe"
        build(safe, output)
        page = (output / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>window.pwned=1</script>", page)
        self.assertIn("&lt;script&gt;window.pwned=1&lt;/script&gt;", page)

        unsafe = self.article("# 标题\n\n[点击](javascript:alert(1))\n")
        with self.assertRaisesRegex(BuildError, "不安全"):
            build(unsafe, self.root / "unsafe")

    def test_missing_image_and_arbitrary_overwrite_fail_closed(self):
        source = self.article("# 标题\n\n![缺图](missing.png)\n")
        with self.assertRaisesRegex(BuildError, "找不到本地图片"):
            build(source, self.root / "missing-output")

        output = self.root / "occupied"
        output.mkdir()
        (output / "important.txt").write_text("keep", encoding="utf-8")
        no_image = self.article("# 标题\n\n正文\n")
        with self.assertRaisesRegex(BuildError, "拒绝覆盖"):
            build(no_image, output, force=True)
        self.assertEqual((output / "important.txt").read_text(encoding="utf-8"), "keep")

    def test_force_rebuilds_only_own_output(self):
        source = self.article("# 标题\n\n第一版\n")
        output = self.root / "publish"
        build(source, output)
        source.write_text("# 标题\n\n第二版\n", encoding="utf-8")
        result = build(source, output, force=True)
        self.assertIn("第二版", (output / "正文-纯文字备用.txt").read_text(encoding="utf-8"))
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["generator"], "x-article-workbench")
        self.assertEqual(result["text_blocks_verified"], 1)

    def test_remote_images_do_not_auto_load_and_outside_files_are_blocked(self):
        remote = self.article("# 标题\n\n![远程](https://images.example.com/a.png)\n")
        output = self.root / "remote"
        result = build(remote, output, cover_mode="none")
        page = (output / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('<img src="https://images.example.com/a.png"', page)
        self.assertIn("远程图片未自动加载", page)
        self.assertEqual(result["body_images"], 1)

        article_dir = self.root / "article-dir"
        article_dir.mkdir()
        outside = self.root / "private.png"
        outside.write_bytes(PNG)
        source = article_dir / "article.md"
        source.write_text("# 标题\n\n![外部](../private.png)\n", encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "文章目录之外"):
            build(source, self.root / "blocked")

    def test_nested_lists_keep_inline_emphasis(self):
        source = self.article(
            "# 标题\n\n- 外层 **重点**\n    1. 内层一步\n    2. 内层二步\n"
        )
        output = self.root / "nested"
        build(source, output)
        page = (output / "index.html").read_text(encoding="utf-8")
        plain = (output / "正文-纯文字备用.txt").read_text(encoding="utf-8")
        self.assertIn("<strong>重点</strong>", page)
        self.assertIn("• 外层 重点", plain)
        self.assertIn("1、内层一步", plain)

    def test_tables_become_readable_lines(self):
        source = self.article(
            "# 标题\n\n| 模型 | 分数 |\n| --- | ---: |\n| Astra | **99** |\n"
        )
        output = self.root / "table"
        build(source, output)
        page = (output / "index.html").read_text(encoding="utf-8")
        plain = (output / "正文-纯文字备用.txt").read_text(encoding="utf-8")
        self.assertNotIn("<table", page)
        self.assertIn("模型 ｜ 分数", plain)
        self.assertIn("Astra ｜ 99", plain)
        self.assertIn("<strong>99</strong>", page)


if __name__ == "__main__":
    unittest.main()
