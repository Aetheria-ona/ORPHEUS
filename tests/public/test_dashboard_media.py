"""Serve synthetic pictures through the dashboard's loopback HTTP routes."""

import base64
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from ow174.dashboard import server

# The same synthetic, single blue pixel encoded losslessly in each format.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGOQdEgGAAEyAL3ollEYAAAAAElFTkSuQmCC"
)
WEBP = base64.b64decode("UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAdQoGZ0rP+BiOh/AAA=")


class DashboardMediaTests(unittest.TestCase):
    def setUp(self):
        self.web = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(server, "WEB_DIR", self.web))
        http = server.start_dashboard(lobby=None, port=0)
        self.assertIsNotNone(http)
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        self.url = f"http://127.0.0.1:{http.server_port}"

    def assert_serves_picture(self, path, picture, mime):
        file = self.web / path.lstrip("/")
        file.parent.mkdir(parents=True)
        file.write_bytes(picture)
        with urllib.request.urlopen(self.url + path, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], mime)
            self.assertEqual(response.read(), picture)

    def assert_not_found(self, path):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.url + path, timeout=3).close()
        with caught.exception as response:
            self.assertEqual(response.code, 404, path)

    def test_loot_box_pictures_are_served_and_nothing_outside_their_folder(self):
        self.assert_serves_picture("/assets/boxes/golden.png", PNG, "image/png")
        (self.web / "assets" / "index.html").write_text("outside boxes", encoding="utf-8")
        (self.web / "assets" / "x.png").write_bytes(PNG)
        for path in (
            "/assets/boxes/missing.png",
            "/assets/boxes/../index.html",
            "/assets/boxes/%2e%2e/x.png",
        ):
            with self.subTest(path=path):
                self.assert_not_found(path)

    def test_item_previews_are_served_as_webp(self):
        self.assert_serves_picture("/assets/previews/0000000000000001.webp", WEBP, "image/webp")
        self.assert_not_found("/assets/previews/0000000000000000.webp")
