#!/usr/bin/env python3
"""Local homepage editor and static HTML generator; Python 3.9+, no dependencies."""

import argparse
import base64
import binascii
import hashlib
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from string import Template
import tempfile
import threading
from urllib.parse import unquote, urlsplit
import webbrowser


ROOT = Path(__file__).resolve().parent
CONTENT_PATH = ROOT / "content.json"
INDEX_PATH = ROOT / "index.html"
TEMPLATE_PATH = ROOT / "templates" / "homepage.html"
STATE_LOCK = threading.RLock()
MAX_REQUEST_BYTES = 15 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class ConflictError(ValueError):
    """The source changed since the editor last loaded it."""


def validate_url(value, image=False):
    if not isinstance(value, str):
        raise ValueError("链接或图片地址必须是文字。")
    value = value.strip()
    if not value:
        return ""
    if re.search(r"[\x00-\x20\x7f\\]", value):
        raise ValueError("链接或图片地址不能含空格、控制字符或反斜杠。")
    decoded = unquote(value)
    if re.search(r"[\x00-\x1f\x7f\\]", decoded):
        raise ValueError("链接或图片地址含无效字符。")
    parts = urlsplit(value)
    allowed = ("http", "https") if image else ("http", "https", "mailto", "tel")
    if parts.scheme:
        if parts.scheme.lower() not in allowed:
            raise ValueError("链接仅支持 http、https、mailto、tel 或站内相对路径；图片仅支持 http、https 或相对路径。")
        if parts.scheme.lower() in ("http", "https") and not parts.netloc:
            raise ValueError("网址缺少域名。")
    elif value.startswith("//") or ".." in decoded.split("/"):
        raise ValueError("请使用完整网址或不含上级目录的站内路径。")
    return value


class InlineHTML(HTMLParser):
    """Keep a small, deterministic inline rich-text vocabulary."""

    ALIASES = {"b": "strong", "i": "em"}
    ALLOWED = {"strong", "em", "a"}
    BLOCKS = {"div", "p", "li"}
    FORBIDDEN = {"script", "style", "iframe", "object", "embed", "svg", "math", "img", "form", "input"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.stack = []

    def line_break(self):
        if self.output and self.output[-1] != "<br>":
            self.output.append("<br>")

    def handle_starttag(self, tag, attrs):
        tag = self.ALIASES.get(tag, tag)
        if tag in self.FORBIDDEN:
            raise ValueError("富文本中不能包含脚本、图片或嵌入页面；请使用图片字段。")
        if tag == "br":
            self.output.append("<br>")
        elif tag in self.BLOCKS:
            self.line_break()
        elif tag in self.ALLOWED:
            if tag == "a":
                url = validate_url(dict(attrs).get("href", ""))
                self.output.append('<a href="{}">'.format(html.escape(url, quote=True)))
            else:
                self.output.append("<{}>".format(tag))
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != "br":
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = self.ALIASES.get(tag, tag)
        if tag in self.BLOCKS:
            self.line_break()
        elif tag in self.stack:
            while self.stack:
                opened = self.stack.pop()
                self.output.append("</{}>".format(opened))
                if opened == tag:
                    break

    def handle_data(self, data):
        self.output.append(html.escape(data, quote=False))

    def result(self):
        while self.stack:
            self.output.append("</{}>".format(self.stack.pop()))
        return "".join(self.output).removesuffix("<br>")


def sanitize_rich_text(value):
    parser = InlineHTML()
    parser.feed(value)
    parser.close()
    return parser.result()


def validate_content(content):
    """Validate every field before rendering or touching any files."""
    def obj(value, path):
        if not isinstance(value, dict):
            raise ValueError("{} 必须是对象。".format(path))
        return value

    def field(value, key, path, rich=False, url=False, image=False):
        result = value.get(key)
        if not isinstance(result, str) or len(result) > 50000:
            raise ValueError("{}.{} 必须是文字，且不超过 50000 字符。".format(path, key))
        if "\x00" in result:
            raise ValueError("{}.{} 含无效字符。".format(path, key))
        if rich:
            return sanitize_rich_text(result)
        if url:
            return validate_url(result, image=image)
        return result

    def array(value, path):
        if not isinstance(value, list) or len(value) > 200:
            raise ValueError("{} 必须是列表，且不超过 200 项。".format(path))
        return value

    def text_fields(value, keys, path):
        value = obj(value, path)
        return {key: field(value, key, path) for key in keys}

    def rich_array(value, key, path):
        values = array(value.get(key), path + "." + key)
        return [field({"text": text}, "text", path, rich=True) for text in values]

    def image_field(value, path):
        value = obj(value, path)
        result = {"src": field(value, "src", path, url=True, image=True), "alt": field(value, "alt", path)}
        for key in ("width", "height"):
            dimension = value.get(key)
            if type(dimension) is not int or not 1 <= dimension <= 10000:
                raise ValueError("{}.{} 必须是 1 到 10000 的整数。".format(path, key))
            result[key] = dimension
        return result

    def link_array(value, path):
        result = []
        for item in array(value, path):
            item = obj(item, path)
            result.append({"label": field(item, "label", path), "url": field(item, "url", path, url=True)})
        return result

    content = obj(content, "内容")
    result = {}
    meta = obj(content.get("meta"), "meta")
    result["meta"] = text_fields(meta, ("title", "description", "socialTitle", "socialDescription", "twitterDescription"), "meta")
    result["meta"]["socialImage"] = field(meta, "socialImage", "meta", url=True, image=True)
    profile = obj(content.get("profile"), "profile")
    result["profile"] = text_fields(profile, ("name", "role", "affiliation"), "profile")
    result["profile"]["image"] = image_field(profile.get("image"), "profile.image")
    result["profile"]["contacts"] = [text_fields(c, ("label", "value"), "联系方式") for c in array(profile.get("contacts"), "联系方式")]
    result["profile"]["links"] = link_array(profile.get("links"), "个人链接")
    bio = obj(content.get("bio"), "bio")
    result["bio"] = {"title": field(bio, "title", "bio"), "paragraphs": rich_array(bio, "paragraphs", "bio")}
    for key in ("projects", "publications", "earlierWork"):
        section = obj(content.get(key), key)
        items = []
        for item in array(section.get("items"), key + ".items"):
            item = obj(item, key)
            if key == "projects":
                output = text_fields(item, ("title", "tagline", "caption"), key)
                output["imageUrl"] = field(item, "imageUrl", key, url=True)
                output["paragraphs"] = rich_array(item, "paragraphs", key)
                output["features"] = rich_array(item, "features", key)
            else:
                output = text_fields(item, ("title", "venue", "description"), key)
                output["authors"] = field(item, "authors", key, rich=True)
            output["image"] = image_field(item.get("image"), key + ".image")
            output["links"] = link_array(item.get("links"), key + ".links")
            items.append(output)
        result[key] = {"title": field(section, "title", key), "items": items}
    result["footer"] = text_fields(content.get("footer"), ("text",), "footer")
    return result


def render_page(content):
    content = validate_content(content)
    esc = lambda value: html.escape(str(value), quote=True)

    def links(items):
        return "\n".join('<a href="{}">{}</a>'.format(esc(link["url"]), esc(link["label"])) for link in items)

    def image(value, css, lazy=True):
        if not value["src"]:
            return ""
        return '<img class="{}" src="{}" alt="{}" width="{}" height="{}"{} />'.format(
            css, esc(value["src"]), esc(value["alt"]), value["width"], value["height"], ' loading="lazy"' if lazy else "")

    profile = content["profile"]
    contacts = "\n".join('<div><dt>{}</dt><dd>{}</dd></div>'.format(esc(c["label"]), esc(c["value"])) for c in profile["contacts"])
    profile_html = '''      <header class="profile-header{photo_class}">
        {photo}
        <div class="profile-summary">
          <h1>{name}</h1>
          <p class="role">{role}</p>
          <p class="affiliation">{affiliation}</p>
          <dl class="contact-list">{contacts}</dl>
          <nav class="profile-links" aria-label="Profile links">{links}</nav>
        </div>
      </header>'''.format(photo_class="" if profile["image"]["src"] else " without-photo", photo=image(profile["image"], "profile-photo", False), name=esc(profile["name"]), role=esc(profile["role"]), affiliation=esc(profile["affiliation"]), contacts=contacts, links=links(profile["links"]))

    def section(css, heading_id, title, body):
        return '      <section class="{}" aria-labelledby="{}">\n        <h2 id="{}">{}</h2>\n{}\n      </section>'.format(css, heading_id, heading_id, esc(title), body)

    bio_html = section("bio-section", "bio-title", content["bio"]["title"], "\n".join("        <p>{}</p>".format(p) for p in content["bio"]["paragraphs"]))
    projects = []
    for item in content["projects"]["items"]:
        figure = image(item["image"], "project-image")
        if figure:
            if item["imageUrl"]:
                figure = '<a href="{}">{}</a>'.format(esc(item["imageUrl"]), figure)
            figure = '<figure class="project-figure">{}<figcaption>{}</figcaption></figure>'.format(figure, esc(item["caption"]))
        paragraphs = "\n".join('<p class="project-description">{}</p>'.format(p) for p in item["paragraphs"])
        features = "\n".join("<li>{}</li>".format(p) for p in item["features"])
        projects.append('''        <article class="project-card">
          <header class="project-heading">
            <div><h3>{title}</h3><p class="project-tagline">{tagline}</p></div>
            <nav class="project-links" aria-label="Project links">{links}</nav>
          </header>
          {figure}
          {paragraphs}
          <ul class="project-features">{features}</ul>
        </article>'''.format(title=esc(item["title"]), tagline=esc(item["tagline"]), links=links(item["links"]), figure=figure, paragraphs=paragraphs, features=features))
    projects_html = section("projects-section", "projects-title", content["projects"]["title"], "\n".join(projects))

    def publications(key, css, heading_id):
        items = []
        for item in content[key]["items"]:
            items.append('''          <article class="publication{image_class}">
            {image}
            <div class="publication-copy">
              <h3>{title}</h3>
              <p class="authors">{authors}</p>
              <p class="venue">{venue}</p>
              <p class="description">{description}</p>
              <div class="publication-links">{links}</div>
            </div>
          </article>'''.format(image_class="" if item["image"]["src"] else " without-image", image=image(item["image"], "publication-image"), title=esc(item["title"]), authors=item["authors"], venue=esc(item["venue"]), description=esc(item["description"]), links=links(item["links"])))
        return section(css, heading_id, content[key]["title"], '        <div class="publication-list">\n{}\n        </div>'.format("\n".join(items)))

    meta = content["meta"]
    return Template(TEMPLATE_PATH.read_text(encoding="utf-8")).substitute(
        page_title=esc(meta["title"]), meta_description=esc(meta["description"]), social_title=esc(meta["socialTitle"]), social_description=esc(meta["socialDescription"]), social_image=esc(meta["socialImage"]), twitter_description=esc(meta["twitterDescription"]), profile=profile_html, bio=bio_html, projects=projects_html, publications=publications("publications", "publications-section", "publications-title"), earlier_work=publications("earlierWork", "earlier-work-section", "earlier-work-title"), footer=esc(content["footer"]["text"]))


def load_content():
    return validate_content(json.loads(CONTENT_PATH.read_text(encoding="utf-8")))


def revision():
    digest = hashlib.sha256()
    for path in (CONTENT_PATH, INDEX_PATH, TEMPLATE_PATH):
        digest.update(path.name.encode("utf-8") + b"\x00")
        digest.update(path.read_bytes() if path.exists() else b"")
        digest.update(b"\x00")
    return digest.hexdigest()


def write_atomic(path, data):
    descriptor, name = tempfile.mkstemp(prefix=".homepage-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_content(content, expected_revision):
    with STATE_LOCK:
        if not isinstance(expected_revision, str) or expected_revision != revision():
            raise ConflictError("文件已被其他编辑器修改。请先复制需要保留的文字，再刷新页面重新加载，避免覆盖其他修改。")
        clean = validate_content(content)
        page = render_page(clean).encode("utf-8")
        source = (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        old_content = CONTENT_PATH.read_bytes()
        old_index = INDEX_PATH.read_bytes() if INDEX_PATH.exists() else None
        try:
            write_atomic(CONTENT_PATH, source)
            write_atomic(INDEX_PATH, page)
        except OSError:
            write_atomic(CONTENT_PATH, old_content)
            if old_index is not None:
                write_atomic(INDEX_PATH, old_index)
            raise
        return {"content": clean, "revision": revision(), "message": "已保存到本地，并生成 index.html。推送到 GitHub 后即可更新线上主页。"}


def save_upload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str) or not isinstance(payload.get("data"), str):
        raise ValueError("请选择图片文件。")
    extension = Path(payload["name"]).suffix.lower()
    if extension not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        raise ValueError("图片支持 PNG、JPEG、GIF 和 WebP 格式。")
    try:
        data = base64.b64decode(payload["data"], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("图片数据无效。")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片大小必须在 10 MB 以内。")
    signatures = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
        ".gif": data[:6] in (b"GIF87a", b"GIF89a"),
        ".webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not signatures[extension]:
        raise ValueError("文件内容与图片格式不符。")
    directory = ROOT / "images" / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-zA-Z0-9_-]", "-", Path(payload["name"]).stem).strip("-")[:60] or "image"
    filename = "{}-{}{}".format(stem, secrets.token_hex(6), extension)
    write_atomic(directory / filename, data)
    return {"src": "images/uploads/" + filename}


class EditorHandler(BaseHTTPRequestHandler):
    server_version = "HomepageEditor/1.0"

    def respond(self, status, payload, content_type="application/json; charset=utf-8"):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in ("127.0.0.1:{}".format(port), "localhost:{}".format(port))

    def do_GET(self):
        if not self.valid_host():
            self.respond(403, {"error": "仅支持本机访问。"})
            return
        route = unquote(urlsplit(self.path).path)
        if route == "/api/content":
            try:
                with STATE_LOCK:
                    self.respond(200, {"content": load_content(), "revision": revision()})
            except (ValueError, OSError) as error:
                self.respond(500, {"error": str(error)})
            return
        assets = {"/": INDEX_PATH, "/index.html": INDEX_PATH, "/editor": ROOT / "tools" / "editor.html", "/editor/": ROOT / "tools" / "editor.html", "/editor.css": ROOT / "tools" / "editor.css", "/editor.js": ROOT / "tools" / "editor.js"}
        path = assets.get(route)
        if path is None and route.startswith(("/images/", "/papers/")):
            candidate = (ROOT / route.lstrip("/")).resolve()
            allowed = ((ROOT / "images").resolve(), (ROOT / "papers").resolve())
            if not any(part.startswith(".") for part in Path(route).parts) and any(parent in candidate.parents for parent in allowed):
                path = candidate
        if path is None or not path.is_file():
            self.respond(404, {"error": "文件不存在。"})
            return
        try:
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type == "application/javascript":
                content_type += "; charset=utf-8"
            self.respond(200, path.read_bytes(), content_type)
        except OSError:
            self.respond(500, {"error": "无法读取文件。"})

    def do_POST(self):
        port = self.server.server_port
        allowed_origins = ("http://127.0.0.1:{}".format(port), "http://localhost:{}".format(port))
        if not self.valid_host() or self.headers.get("Origin") not in allowed_origins:
            self.respond(403, {"error": "请从本机编辑器页面保存。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self.respond(415, {"error": "请求必须使用 JSON。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                self.respond(413, {"error": "请求过大或为空。"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是对象。")
            route = urlsplit(self.path).path
            if route == "/api/preview":
                page = render_page(payload.get("content"))
                page = page.replace("<head>", '<head>\n    <base href="/" />', 1)
                self.respond(200, page, "text/html; charset=utf-8")
            elif route == "/api/save":
                self.respond(200, save_content(payload.get("content"), payload.get("revision")))
            elif route == "/api/upload":
                self.respond(200, save_upload(payload))
            else:
                self.respond(404, {"error": "接口不存在。"})
        except ConflictError as error:
            self.respond(409, {"error": str(error)})
        except (ValueError, UnicodeError, RecursionError) as error:
            self.respond(400, {"error": str(error)})
        except OSError as error:
            self.respond(500, {"error": "无法保存文件：{}".format(error)})

    def log_message(self, format, *args):
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


def create_server(port=0):
    return ThreadingHTTPServer(("127.0.0.1", port), EditorHandler)


def main():
    parser = argparse.ArgumentParser(description="本地主页编辑器：在浏览器中编辑内容并生成静态主页。")
    parser.add_argument("--port", type=int, default=8765, help="编辑器端口，默认 8765；0 表示自动分配")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--build", action="store_true", help="从 content.json 生成 index.html 后退出")
    parser.add_argument("--check", action="store_true", help="检查内容和生成的 index.html 是否一致")
    args = parser.parse_args()
    try:
        page = render_page(load_content())
        if args.check:
            if not INDEX_PATH.exists() or INDEX_PATH.read_text(encoding="utf-8") != page:
                parser.exit(1, "index.html 与内容不一致，请运行 python3 edit_homepage.py --build。\n")
            print("内容校验通过，index.html 与 content.json 一致。")
            return
        if args.build:
            write_atomic(INDEX_PATH, page.encode("utf-8"))
            print("已从 content.json 生成 index.html。")
            return
        server = create_server(args.port)
    except (ValueError, OSError) as error:
        parser.exit(1, "无法启动：{}\n".format(error))
    url = "http://127.0.0.1:{}/editor".format(server.server_port)
    print("\n主页编辑器：{}\n保存仅更新本地文件；推送到 GitHub 后更新线上主页。\n按 Ctrl+C 停止。\n".format(url), flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n编辑器已关闭。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
