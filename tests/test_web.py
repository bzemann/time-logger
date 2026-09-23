"""Static asset checks for web/ (generic; do not depend on exact markup)."""

from __future__ import annotations

import http.client
import re
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path

from worktime import server

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "web"
APP_JS = WEB_ROOT / "app.js"


class _LinkScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.refs.append(attrs["src"])
        if tag == "link" and attrs.get("href"):
            self.refs.append(attrs["href"])


def _norm(ref: str) -> str:
    if ref.startswith("./"):
        ref = ref[2:]
    if ref.startswith("/"):
        ref = ref[1:]
    return ref


class ServedAssetsTests(unittest.TestCase):
    def setUp(self):
        self.srv = server.make_server("127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.thread.join(timeout=5)
        self.srv.server_close()

    def _get(self, path: str) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
        return conn.getresponse()

    def test_index_and_referenced_assets_are_served(self):
        resp = self._get("/")
        self.assertEqual(resp.status, 200)
        content_type = resp.getheader("Content-Type", "")
        self.assertTrue(content_type.startswith("text/html"), content_type)
        body = resp.read().decode("utf-8")

        parser = _LinkScriptParser()
        parser.feed(body)

        refs = [
            r for r in parser.refs if not r.startswith("data:") and not r.startswith("#")
        ]
        self.assertTrue(refs, "expected at least one <script src> or <link href> in index.html")

        for ref in refs:
            norm = _norm(ref)
            resp = self._get("/" + norm)
            self.assertEqual(resp.status, 200, f"{ref} -> {resp.status}")
            data = resp.read()
            self.assertTrue(data, f"{ref} has empty body")
            ct = resp.getheader("Content-Type", "")
            if norm.endswith(".js"):
                self.assertIn("javascript", ct, f"{ref} content-type: {ct}")
            elif norm.endswith(".css"):
                self.assertTrue(ct.startswith("text/css"), f"{ref} content-type: {ct}")


class RequiredAssetsTests(unittest.TestCase):
    def test_index_references_required_assets(self):
        html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        for needle in ("vendor/chart.umd.js", "app.js", "style.css"):
            self.assertIn(needle, html, f"index.html does not reference {needle}")

    def test_chartjs_license_present(self):
        license_path = WEB_ROOT / "vendor" / "LICENSE-chartjs.md"
        self.assertTrue(license_path.exists(), "web/vendor/LICENSE-chartjs.md missing")
        text = license_path.read_text(encoding="utf-8")
        self.assertIn("MIT", text)


class OfflineGuaranteeTests(unittest.TestCase):
    EXTERNAL_RE = [
        re.compile(r"""(src|href)\s*=\s*["']\s*(https?:)?//""", re.IGNORECASE),
        re.compile(r"""url\(\s*["']?\s*(https?:)?//""", re.IGNORECASE),
        re.compile(r"""fetch\(\s*["'`]\s*(https?:)?//""", re.IGNORECASE),
        re.compile(r"""@import\s+["']?(https?:)?//""", re.IGNORECASE),
    ]

    def test_no_external_references_outside_vendor(self):
        vendor_dir = WEB_ROOT / "vendor"
        files = [
            p
            for p in WEB_ROOT.rglob("*")
            if p.is_file()
            and p.suffix in (".html", ".css", ".js")
            and vendor_dir not in p.parents
        ]
        self.assertTrue(files, "expected .html/.css/.js files under web/")

        failures = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            for regex in self.EXTERNAL_RE:
                m = regex.search(text)
                if m:
                    failures.append(f"{path}: matched {regex.pattern!r} -> {m.group(0)!r}")

        self.assertFalse(failures, "\n".join(failures))


class NoInnerHtmlTests(unittest.TestCase):
    def test_app_js_has_no_innerhtml(self):
        text = APP_JS.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", text)


if __name__ == "__main__":
    unittest.main()
