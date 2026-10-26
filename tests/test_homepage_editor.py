"""Regression tests for the dependency-free local homepage editor.

Run with: python3 -m unittest discover -s tests -v
"""

import base64
import copy
import html
from html.parser import HTMLParser
import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest import mock

import edit_homepage as editor


class PageInventory(HTMLParser):
    """Compare page content without depending on generated HTML indentation."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.text = []
        self.links = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "main":
            self.in_main = True
        if not self.in_main:
            return
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        elif tag == "img":
            self.images.append(tuple(attrs.get(key) for key in ("src", "alt", "width", "height")))

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False

    def handle_data(self, data):
        if self.in_main:
            self.text.append(data)

    def visible_text(self):
        chunks = (re.sub(r"\s+", " ", part).strip() for part in self.text)
        return " ".join(part for part in chunks if part)


class EditorTestCase(unittest.TestCase):
    """Every test writes into its own copy, never into the user's homepage."""

    @classmethod
    def setUpClass(cls):
        cls.fixture_root = Path(editor.ROOT)
        cls.fixture = copy.deepcopy(editor.load_content())
        cls.original_index = Path(editor.INDEX_PATH).read_text(encoding="utf-8")
        cls.template = Path(editor.TEMPLATE_PATH).read_text(encoding="utf-8")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content_path = self.root / "content.json"
        self.index_path = self.root / "index.html"
        self.template_path = self.root / "templates" / "homepage.html"
        self.template_path.parent.mkdir()
        self.template_path.write_text(self.template, encoding="utf-8")
        self.content_path.write_text(json.dumps(self.fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.index_path.write_text(self.original_index, encoding="utf-8")
        self.content = copy.deepcopy(self.fixture)
        self.patches = mock.patch.multiple(
            editor,
            ROOT=self.root,
            CONTENT_PATH=self.content_path,
            INDEX_PATH=self.index_path,
            TEMPLATE_PATH=self.template_path,
        )
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def disk_state(self):
        return (self.content_path.read_bytes(), self.index_path.read_bytes())


class ContentTests(EditorTestCase):
    def test_migrated_content_preserves_homepage_text_links_and_images(self):
        original = PageInventory()
        original.feed(self.original_index)
        generated = PageInventory()
        generated.feed(editor.render_page(self.content))
        self.assertEqual(generated.visible_text(), original.visible_text())
        self.assertEqual(generated.links, original.links)
        self.assertEqual(generated.images, original.images)

    def test_renderer_escapes_plain_text_and_keeps_unicode(self):
        value = '王俊杰 & <research> "测试"'
        self.content["profile"]["name"] = value
        self.content["meta"]["title"] = value
        self.content["profile"]["image"]["alt"] = value
        page = editor.render_page(self.content)
        self.assertIn(html.escape(value), page)
        self.assertNotIn("<research>", page)
        self.assertIn("王俊杰", page)

    def test_inline_rich_text_retains_bold_italic_and_links(self):
        value = '<strong>论文</strong> &amp; <em>research</em> <a href="https://example.org/?x=1&amp;y=2">网页</a><br>下一行'
        rendered = editor.sanitize_rich_text(value)
        self.assertIn("<strong>论文</strong>", rendered)
        self.assertIn("<em>research</em>", rendered)
        self.assertIn('href="https://example.org/?x=1&amp;y=2"', rendered)
        self.assertIn("下一行", rendered)
        self.assertNotIn("&amp;amp;", rendered)

    def test_rich_text_rejects_script_and_unsafe_links(self):
        for value in ('<script>alert(1)</script>', '<a href="javascript:alert(1)">test</a>'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    editor.sanitize_rich_text(value)

    def test_urls_reject_active_schemes_and_control_characters(self):
        for value in ("javascript:alert(1)", "data:text/html,test", "file:///etc/passwd", "https://example.org/\nheader"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    editor.validate_url(value)
        for value in ("https://example.org/paper", "papers/cfq4cp.pdf", "#bio-title", "mailto:test@example.org"):
            with self.subTest(value=value):
                editor.validate_url(value)

    def test_invalid_shape_is_rejected_without_writing(self):
        before = self.disk_state()
        invalid = copy.deepcopy(self.content)
        invalid["publications"]["items"] = "not an array"
        with self.assertRaises(ValueError):
            editor.save_content(invalid, editor.revision())
        self.assertEqual(self.disk_state(), before)

    def test_publications_can_be_added_removed_and_reordered(self):
        items = self.content["publications"]["items"]
        added = copy.deepcopy(items[0])
        added["title"] = "A newly added publication"
        items.insert(0, added)
        removed = items.pop(2)["title"]
        page = editor.render_page(self.content)
        self.assertLess(page.index(added["title"]), page.index(items[1]["title"]))
        self.assertNotIn(html.escape(removed), page)
        self.content["earlierWork"]["items"] = []
        self.content["projects"]["items"] = []
        empty_page = editor.render_page(self.content)
        self.assertNotIn('class="project-card"', empty_page)

    def test_save_persists_data_and_the_same_rendered_page(self):
        old_revision = editor.revision()
        self.content["profile"]["role"] = "中文简介：研究者 & developer"
        result = editor.save_content(self.content, old_revision)
        self.assertEqual(editor.load_content(), result["content"])
        self.assertEqual(self.index_path.read_text(encoding="utf-8"), editor.render_page(result["content"]))
        self.assertNotEqual(result["revision"], old_revision)
        self.assertEqual(result["revision"], editor.revision())

    def test_stale_save_does_not_overwrite_newer_data(self):
        stale_revision = editor.revision()
        self.content["profile"]["name"] = "First save"
        editor.save_content(self.content, stale_revision)
        before = self.disk_state()
        self.content["profile"]["name"] = "Stale tab"
        with self.assertRaises(editor.ConflictError):
            editor.save_content(self.content, stale_revision)
        self.assertEqual(self.disk_state(), before)

    def test_manual_file_changes_invalidate_revision(self):
        stale_revision = editor.revision()
        with self.index_path.open("a", encoding="utf-8") as file:
            file.write("\n<!-- external change -->\n")
        before = self.disk_state()
        with self.assertRaises(editor.ConflictError):
            editor.save_content(self.content, stale_revision)
        self.assertEqual(self.disk_state(), before)

    def test_empty_images_render_single_column_variants_without_broken_images(self):
        self.content["profile"]["image"]["src"] = ""
        self.content["publications"]["items"][0]["image"]["src"] = ""
        page = editor.render_page(self.content)
        self.assertIn('class="profile-header without-photo"', page)
        self.assertIn('class="publication without-image"', page)
        self.assertNotIn('src=""', page)
        self.assertNotIn('class="profile-photo"', page)
        self.assertIn(self.content["publications"]["items"][0]["title"], page)

    def test_failed_index_write_rolls_back_the_content_file(self):
        before = self.disk_state()
        old_revision = editor.revision()
        self.content["profile"]["name"] = "Should not survive the failed save"
        write_atomic = editor.write_atomic
        failed = False

        def fail_index_once(path, data):
            nonlocal failed
            if Path(path) == self.index_path and not failed:
                failed = True
                raise OSError("simulated index write failure")
            return write_atomic(path, data)

        with mock.patch.object(editor, "write_atomic", side_effect=fail_index_once):
            with self.assertRaises(OSError):
                editor.save_content(self.content, old_revision)
        self.assertTrue(failed)
        self.assertEqual(self.disk_state(), before)
        self.assertEqual(editor.revision(), old_revision)


class HttpTests(EditorTestCase):
    def setUp(self):
        super().setUp()
        self.server = editor.create_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.port = self.server.server_address[1]
        self.origin = "http://127.0.0.1:{}".format(self.port)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, payload=None, origin=True, content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = content_type
        if origin:
            headers["Origin"] = self.origin if origin is True else origin
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_server_binds_to_loopback_and_loads_content(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, _, body = self.request("GET", "/api/content")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["content"], editor.load_content())
        self.assertEqual(data["revision"], editor.revision())

    def test_preview_renders_draft_without_saving(self):
        before = self.disk_state()
        self.content["profile"]["name"] = "仅预览的姓名"
        status, headers, body = self.request("POST", "/api/preview", {"content": self.content})
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("仅预览的姓名", body.decode("utf-8"))
        self.assertRegex(body.decode("utf-8"), r"<base\s+href=[\"']/[\"']\s*/?>")
        self.assertEqual(self.disk_state(), before)

    def test_save_endpoint_and_stale_conflict(self):
        payload = {"content": self.content, "revision": editor.revision()}
        payload["content"]["footer"]["text"] = "通过表单更新"
        status, _, body = self.request("POST", "/api/save", payload)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["content"]["footer"]["text"], "通过表单更新")
        before = self.disk_state()
        status, _, body = self.request("POST", "/api/save", payload)
        self.assertEqual(status, 409)
        self.assertIn("error", json.loads(body))
        self.assertEqual(self.disk_state(), before)

    def test_write_requests_require_same_origin_json(self):
        before = self.disk_state()
        payload = {"content": self.content, "revision": editor.revision()}
        cases = [(False, "application/json"), ("https://other.example", "application/json"), (True, "text/plain")]
        for origin, content_type in cases:
            with self.subTest(origin=origin, content_type=content_type):
                status, _, _ = self.request("POST", "/api/save", payload, origin=origin, content_type=content_type)
                self.assertIn(status, (400, 403, 415))
        self.assertEqual(self.disk_state(), before)

    def test_hidden_files_and_path_traversal_are_not_served(self):
        for path in ("/.git/config", "/content.json", "/../content.json", "/images/%2e%2e/content.json"):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path)
                self.assertIn(status, (400, 403, 404))

    def test_image_upload_saves_an_image_and_rejects_active_content(self):
        # A real 1x1 PNG, rather than a file carrying only an image extension.
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=")
        status, _, body = self.request("POST", "/api/upload", {"name": "头像.png", "data": base64.b64encode(png).decode("ascii")})
        self.assertEqual(status, 200)
        src = json.loads(body)["src"]
        self.assertTrue(src.startswith("images/uploads/"))
        self.assertEqual((self.root / src).read_bytes(), png)
        status, _, _ = self.request("POST", "/api/upload", {"name": "bad.svg", "data": base64.b64encode(b"<svg onload='alert(1)'/>").decode("ascii")})
        self.assertIn(status, (400, 415))


if __name__ == "__main__":
    unittest.main()
