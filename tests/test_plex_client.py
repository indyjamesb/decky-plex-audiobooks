import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from plex import PlexClient, PlexError, normalize_server_url, parse_track_progress_flag

TOKEN = "super-secret-token-value"


class Handler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, str]] = []

    def log_message(self, fmt, *args):
        return

    def _authorized(self) -> bool:
        return self.headers.get("X-Plex-Token") == TOKEN

    def _record(self) -> None:
        self.requests.append((self.command, self.path, self.headers.get("X-Plex-Token") or ""))

    def do_POST(self):
        self._record()
        if not self._authorized():
            self.send_response(401)
            self.end_headers()
            return
        body = b"<MediaContainer />"
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._record()
        if not self._authorized():
            self.send_response(401)
            self.end_headers()
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            body = b'<MediaContainer friendlyName="Study" machineIdentifier="machine-1"></MediaContainer>'
            content_type = "text/xml"
        elif path == "/library/sections":
            body = json.dumps({
                "MediaContainer": {
                    "Directory": [
                        {"key": "3", "type": "artist", "title": "Audiobooks"},
                        {"key": "1", "type": "movie", "title": "Movies"},
                    ]
                }
            }).encode()
            content_type = "application/json"
        elif path == "/library/sections/3/prefs":
            body = json.dumps({
                "MediaContainer": {
                    "Setting": [
                        {"id": "enableTrackOffsets", "value": "1"},
                    ]
                }
            }).encode()
            content_type = "application/json"
        elif path == "/library/sections/3/all":
            body = json.dumps({
                "MediaContainer": {
                    "totalSize": 1,
                    "Metadata": [{
                        "ratingKey": "10",
                        "title": "The Left Hand of Darkness",
                        "parentTitle": "Ursula K. Le Guin",
                        "thumb": "/library/metadata/10/thumb",
                        "duration": 120000,
                        "leafCount": 2,
                        "viewedLeafCount": 1,
                        "year": 1969,
                    }],
                }
            }).encode()
            content_type = "application/json"
        elif path == "/library/metadata/10":
            body = json.dumps({
                "MediaContainer": {
                    "Metadata": [{
                        "ratingKey": "10",
                        "title": "The Left Hand of Darkness",
                        "parentTitle": "Ursula K. Le Guin",
                        "thumb": "/library/metadata/10/thumb",
                    }]
                }
            }).encode()
            content_type = "application/json"
        elif path == "/library/metadata/10/children":
            body = json.dumps({
                "MediaContainer": {
                    "totalSize": 2,
                    "size": 2,
                    "Metadata": [
                        _track("22", "Chapter 2", 2, 60_000, 15_000, 0),
                        _track("21", "Chapter 1", 1, 60_000, 0, 1),
                    ],
                }
            }).encode()
            content_type = "application/json"
        elif path == "/:/timeline":
            body = b""
            content_type = "text/plain"
        elif path in ("/:/scrobble", "/:/unscrobble"):
            body = b""
            content_type = "text/plain"
        elif path == "/photo/:/transcode":
            body = b"\xff\xd8\xff\xd9"
            content_type = "image/jpeg"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _track(key, title, index, duration, offset, views):
    return {
        "ratingKey": key,
        "title": title,
        "index": index,
        "parentIndex": 1,
        "duration": duration,
        "viewOffset": offset,
        "viewCount": views,
        "Media": [{"Part": [{"key": f"/library/parts/{key}/file.mp3"}]}],
    }


class PlexClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=3)

    def setUp(self):
        Handler.requests = []
        self.client = PlexClient(self.url, TOKEN, "client-1", timeout=3)

    def test_normalize_url(self):
        self.assertEqual(normalize_server_url("10.0.0.91:32400"), "http://10.0.0.91:32400")
        self.assertEqual(normalize_server_url("http://10.0.0.91:32400/"), "http://10.0.0.91:32400")

    def test_identity_libraries_and_music_sections_only(self):
        identity = self.client.identity()
        self.assertEqual(identity["friendly_name"], "Study")
        self.assertEqual(identity["machine_identifier"], "machine-1")
        libraries = self.client.list_libraries()
        self.assertEqual(len(libraries), 1)
        self.assertEqual(libraries[0]["title"], "Audiobooks")
        self.assertTrue(libraries[0]["track_progress"])

    def test_books_tracks_resume_and_cover_do_not_put_the_token_in_the_url(self):
        listed = self.client.list_books("3")
        self.assertEqual(listed["books"][0]["author"], "Ursula K. Le Guin")
        book, tracks = self.client.get_book("10")
        self.assertEqual(book["title"], "The Left Hand of Darkness")
        self.assertEqual([track.rating_key for track in tracks], ["21", "22"])
        self.assertEqual(tracks[1].view_offset_ms, 15_000)
        cover = self.client.fetch_cover("/library/metadata/10/thumb")
        self.assertTrue(cover.startswith("data:image/jpeg;base64,"))
        self.client.timeline("22", "playing", 15_000, 60_000)
        self.client.scrobble("21", True)
        for method, path, header in Handler.requests:
            self.assertNotIn(TOKEN, path)
            self.assertEqual(header, TOKEN, method)
        timeline = [path for method, path, _header in Handler.requests if path.startswith("/:/timeline")]
        self.assertTrue(timeline)
        self.assertIn("playbackTime=15000", timeline[-1])
        self.assertIn("time=15000", timeline[-1])
        self.assertIn("state=playing", timeline[-1])
        # In-progress reports inflate duration so Plex's 90% watched rule stays past the real end.
        self.assertNotIn("duration=60000", timeline[-1])
        scrobbles = [path for _method, path, _header in Handler.requests if path.startswith("/:/scrobble")]
        self.assertIn("key=21", scrobbles[-1])
        self.assertNotIn("library/metadata", scrobbles[-1])

    def test_stream_url_and_headers(self):
        url = self.client.stream_url("/library/parts/22/file.mp3")
        self.assertNotIn(TOKEN, url)
        self.assertTrue(url.endswith("/library/parts/22/file.mp3"))
        self.assertIn(TOKEN, self.client.media_headers())

    def test_rejected_token(self):
        client = PlexClient(self.url, "wrong-token-value", "client-1", timeout=3)
        with self.assertRaises(PlexError) as raised:
            client.identity()
        self.assertEqual(raised.exception.status, 401)
        self.assertNotIn("wrong-token-value", raised.exception.message)

    def test_unreachable_message_hides_the_token(self):
        client = PlexClient("http://127.0.0.1:9", TOKEN, "client-1", timeout=0.4)
        with self.assertRaises(PlexError) as raised:
            client.identity()
        self.assertTrue(raised.exception.unreachable)
        self.assertNotIn(TOKEN, raised.exception.message)
        self.assertIn("Can't reach", raised.exception.message)

    def test_xml_prefs_flag(self):
        text = '<Setting id="enableTrackOffsets" label="Store track progress" type="bool" value="0"/>'
        self.assertFalse(parse_track_progress_flag(None, text))
        self.assertIsNone(parse_track_progress_flag(None, "<Setting id='other'/>"))


if __name__ == "__main__":
    unittest.main()
