import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from plex_auth import (
    PRODUCT_NAME,
    PlexAccount,
    PlexError,
    choose_connection,
    parse_pin,
    public_server,
    sign_in_payload,
)

ACCOUNT_TOKEN = "account-token-value-abcdefgh"
SERVER_TOKEN = "server-token-value-abcdefgh"
CLIENT_ID = "stable-client-identifier"


def _connection(uri, local, relay, protocol="http"):
    address = uri.split("://", 1)[-1].split(":", 1)[0]
    port = uri.rsplit(":", 1)[-1]
    return {
        "protocol": protocol,
        "address": address,
        "port": int(port) if port.isdigit() else 32400,
        "uri": uri,
        "local": local,
        "relay": relay,
    }


RESOURCES = [
    {
        "name": "Players",
        "provides": "player",
        "clientIdentifier": "not-a-server",
        "accessToken": "unused-token-value",
        "connections": [],
    },
    {
        "name": "Study",
        "provides": "server",
        "clientIdentifier": "machine-study",
        "accessToken": SERVER_TOKEN,
        "connections": [
            _connection("https://203.0.113.8:32400", False, False, "https"),
            _connection("https://10-0-0-91.abcdef.plex.direct:32400", True, False, "https"),
            _connection("https://relay.plex.direct:443", False, True, "https"),
            _connection("http://10.0.0.91:32400", True, False, "http"),
        ],
    },
]


class Handler(BaseHTTPRequestHandler):
    posts: list[tuple[str, str, str]] = []
    gets: list[tuple[str, str]] = []
    polls = 0

    def log_message(self, fmt, *args):
        return

    def _record_get(self):
        self.gets.append((self.path, self.headers.get("X-Plex-Token") or ""))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        self.posts.append((
            body,
            self.headers.get("X-Plex-Client-Identifier") or "",
            self.headers.get("X-Plex-Product") or "",
        ))
        payload = json.dumps({"id": 42, "code": "A1B2", "authToken": None}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._record_get()
        path = self.path.split("?", 1)[0]
        if path == "/api/v2/pins/42":
            Handler.polls += 1
            token = ACCOUNT_TOKEN if Handler.polls > 1 else None
            payload = {"id": 42, "code": "A1B2", "authToken": token}
        elif path == "/api/v2/pins/404":
            self.send_response(404)
            self.end_headers()
            return
        elif path == "/api/v2/user":
            if self.headers.get("X-Plex-Token") != ACCOUNT_TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            payload = {"username": "reader", "authToken": ACCOUNT_TOKEN, "email": "reader@example.com"}
        elif path == "/api/v2/resources":
            if self.headers.get("X-Plex-Token") != ACCOUNT_TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            payload = RESOURCES
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ConnectionTests(unittest.TestCase):
    def test_local_lan_address_beats_plex_direct_and_relay(self):
        chosen = choose_connection(RESOURCES[1]["connections"])
        self.assertEqual(chosen["uri"], "http://10.0.0.91:32400")

    def test_private_http_wins_when_the_local_flag_is_missing(self):
        connections = [
            _connection("https://relay.example:443", False, True, "https"),
            _connection("http://10.0.0.91:32400", False, False, "http"),
        ]
        self.assertEqual(choose_connection(connections)["uri"], "http://10.0.0.91:32400")

    def test_public_payload_drops_tokens(self):
        account = {
            "client_identifier": "machine-study",
            "name": "Study",
            "uri": "http://10.0.0.91:32400",
            "local": True,
            "access_token": SERVER_TOKEN,
        }
        payload = sign_in_payload(signed_in=True, username="reader", servers=[account])
        encoded = json.dumps(payload)
        self.assertNotIn(SERVER_TOKEN, encoded)
        self.assertNotIn("access_token", encoded)
        self.assertNotIn("authToken", encoded)
        self.assertEqual(payload["servers"][0]["uri"], "http://10.0.0.91:32400")
        self.assertEqual(public_server(account)["name"], "Study")

    def test_expired_pin_and_claimed_pin(self):
        self.assertEqual(parse_pin({"authToken": None, "expiresAt": "2000-01-01T00:00:00Z"})["state"], "expired")
        claimed = parse_pin({"authToken": ACCOUNT_TOKEN})
        self.assertEqual(claimed["state"], "authorized")
        self.assertEqual(claimed["auth_token"], ACCOUNT_TOKEN)
        self.assertEqual(parse_pin({"authToken": None})["state"], "pending")


class PlexTvFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=3)

    def setUp(self):
        Handler.posts = []
        Handler.gets = []
        Handler.polls = 0
        self.account = PlexAccount(CLIENT_ID, timeout=3, base_url=self.base)

    def test_pin_poll_user_and_local_server(self):
        pin = self.account.create_pin()
        self.assertEqual(pin["code"], "A1B2")
        body, client_id, product = Handler.posts[0]
        self.assertIn("strong=false", body)
        self.assertNotIn("strong=true", body)
        self.assertEqual(client_id, CLIENT_ID)
        self.assertEqual(product, PRODUCT_NAME)

        self.assertEqual(self.account.check_pin(pin["id"], pin["code"])["state"], "pending")
        claimed = self.account.check_pin(pin["id"], pin["code"])
        self.assertEqual(claimed["state"], "authorized")
        self.assertEqual(claimed["auth_token"], ACCOUNT_TOKEN)

        self.assertEqual(self.account.username(ACCOUNT_TOKEN), "reader")
        servers = self.account.list_servers(ACCOUNT_TOKEN)
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["uri"], "http://10.0.0.91:32400")
        self.assertTrue(servers[0]["local"])
        self.assertEqual(servers[0]["access_token"], SERVER_TOKEN)
        shown = json.dumps(sign_in_payload(signed_in=True, servers=servers))
        self.assertNotIn(ACCOUNT_TOKEN, shown)
        self.assertNotIn(SERVER_TOKEN, shown)

        for path, header in Handler.gets:
            self.assertNotIn(ACCOUNT_TOKEN, path)
            self.assertNotIn(SERVER_TOKEN, path)
            if path.startswith("/api/v2/resources") or path.startswith("/api/v2/user"):
                self.assertEqual(header, ACCOUNT_TOKEN)

    def test_missing_pin_is_expired(self):
        status = self.account.check_pin("404", "A1B2")
        self.assertEqual(status["state"], "expired")
        self.assertEqual(status["auth_token"], "")

    def test_unreachable_message_hides_the_token(self):
        account = PlexAccount(CLIENT_ID, timeout=0.4, base_url="http://127.0.0.1:9")
        with self.assertRaises(PlexError) as raised:
            account.create_pin()
        self.assertTrue(raised.exception.unreachable)
        self.assertNotIn(ACCOUNT_TOKEN, raised.exception.message)
        self.assertIn("plex.tv", raised.exception.message)


if __name__ == "__main__":
    unittest.main()
