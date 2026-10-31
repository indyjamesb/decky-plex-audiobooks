"""Plex account sign-in and server discovery.

The link flow is Plex's PIN API: ``POST /api/v2/pins`` with ``strong=false``
returns a short code the user types at https://plex.tv/link. Polling
``GET /api/v2/pins/{id}`` with the same ``X-Plex-Client-Identifier`` yields
``authToken`` once the code is claimed.

``GET /api/v2/resources`` then lists the account's servers. Each server is
shown once, using its local connection when one exists (for example
``http://10.0.0.91:32400``) instead of a relay.

Tokens travel only in the ``X-Plex-Token`` header. Do not log pin responses
or resource payloads: both can contain an auth token.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PRODUCT_NAME = "Plex Audiobooks"
PRODUCT_VERSION = "0.1.4"
PLEX_TV = "https://plex.tv"
LINK_URL = "https://plex.tv/link"

_UNREACHABLE = "Can't reach plex.tv to sign in. Check that this Deck has internet access."


_CA_CANDIDATES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ca-certificates/extracted/tls-ca-bundle.pem",
)
_SSL_CONTEXT: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """Verified TLS context that also works inside Decky's bundled Python.

    Decky Loader's frozen Python may not find SteamOS's CA bundle on its own,
    which makes every HTTPS call fail certificate checks. Load the system
    bundle explicitly (certificate verification stays on).
    """

    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        context = ssl.create_default_context()
        for path in _CA_CANDIDATES:
            if os.path.isfile(path):
                try:
                    context.load_verify_locations(cafile=path)
                    break
                except (OSError, ssl.SSLError):
                    continue
        _SSL_CONTEXT = context
    return _SSL_CONTEXT


def _reason(err: BaseException) -> str:
    reason = getattr(err, "reason", None) or err
    text = str(reason).strip()
    return f" ({text[:160]})" if text else ""


class PlexError(Exception):
    def __init__(self, message: str, *, unreachable: bool = False, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.unreachable = unreachable
        self.status = status


def client_headers(client_id: str, token: str = "") -> dict[str, str]:
    """Headers that identify this install to Plex. Token is optional and never logged here."""

    headers = {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT_NAME,
        "X-Plex-Version": PRODUCT_VERSION,
        "X-Plex-Client-Identifier": client_id or "plex-audiobooks",
        "X-Plex-Platform": "Linux",
        "X-Plex-Platform-Version": "SteamOS",
        "X-Plex-Device": "Steam Deck",
        "X-Plex-Device-Name": PRODUCT_NAME,
        "User-Agent": f"PlexAudiobooks/{PRODUCT_VERSION}",
    }
    if token:
        headers["X-Plex-Token"] = token
    return headers


def _is_private_host(value: str) -> bool:
    host = value or ""
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].split(":", 1)[0]
    parts = host.split(".")
    if len(parts) != 4:
        return False
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return False
    if any(number < 0 or number > 255 for number in numbers):
        return False
    if numbers[0] == 10 or numbers[0] == 127:
        return True
    if numbers[0] == 192 and numbers[1] == 168:
        return True
    if numbers[0] == 172 and 16 <= numbers[1] <= 31:
        return True
    return False


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return False


def connection_uri(connection: dict) -> str:
    uri = str(connection.get("uri") or "").strip().rstrip("/")
    if uri:
        return uri
    address = str(connection.get("address") or "").strip()
    if not address:
        return ""
    protocol = str(connection.get("protocol") or "http").strip() or "http"
    port = connection.get("port")
    if port:
        return f"{protocol}://{address}:{port}".rstrip("/")
    return f"{protocol}://{address}"


def connection_rank(connection: dict) -> tuple:
    """Lower sorts first. A local private HTTP address beats plex.direct and relays."""

    uri = connection_uri(connection)
    local = _truthy(connection.get("local"))
    relay = _truthy(connection.get("relay"))
    https = uri.startswith("https://")
    private = _is_private_host(uri)
    if relay:
        tier = 5
    elif local and private and not https:
        tier = 0
    elif private and not https:
        tier = 1
    elif local and not https:
        tier = 2
    elif local:
        tier = 3
    else:
        tier = 4
    return (tier, uri)


def choose_connection(connections: list[dict]) -> dict | None:
    usable = [item for item in connections if isinstance(item, dict) and connection_uri(item)]
    if not usable:
        return None
    return min(usable, key=connection_rank)


def _provides_server(value: object) -> bool:
    if isinstance(value, list):
        return "server" in value
    return "server" in [part.strip() for part in str(value or "").split(",")]


def parse_servers(payload: object, account_token: str) -> list[dict]:
    """Servers from ``/api/v2/resources``, best connection already chosen.

    ``access_token`` is the token that server expects (a shared server's token
    when Plex sends one, otherwise the account token). Callers must not log it.
    """

    if isinstance(payload, dict):
        resources = payload.get("resources") or payload.get("MediaContainer") or []
        if isinstance(resources, dict):
            resources = resources.get("Device") or resources.get("Resource") or []
    elif isinstance(payload, list):
        resources = payload
    else:
        resources = []

    servers = []
    for resource in resources:
        if not isinstance(resource, dict) or not _provides_server(resource.get("provides")):
            continue
        chosen = choose_connection(resource.get("connections") or [])
        if chosen is None:
            continue
        access = str(resource.get("accessToken") or "").strip() or account_token
        servers.append({
            "client_identifier": str(resource.get("clientIdentifier") or ""),
            "name": resource.get("name") or "Plex",
            "uri": connection_uri(chosen),
            "local": _truthy(chosen.get("local")) or _is_private_host(connection_uri(chosen)),
            "access_token": access,
        })
    servers.sort(key=lambda item: (not item["local"], item["name"].lower(), item["uri"]))
    return servers


def public_server(server: dict) -> dict:
    """Server fields safe to show in the panel. No token."""

    return {
        "client_identifier": server.get("client_identifier") or "",
        "name": server.get("name") or "Plex",
        "uri": server.get("uri") or "",
        "local": bool(server.get("local")),
    }


def sign_in_payload(
    *,
    success: bool = True,
    pending: bool = False,
    signed_in: bool = False,
    username: str = "",
    code: str = "",
    servers: list[dict] | None = None,
    message: str = "",
    expired: bool = False,
) -> dict:
    """Result dict for the panel. Server entries are stripped of tokens."""

    payload = {
        "success": success,
        "pending": pending,
        "signed_in": signed_in,
        "username": username or "",
        "code": code or "",
        "link": LINK_URL if code else "",
        "servers": [public_server(server) for server in (servers or [])],
        "expired": expired,
    }
    if message:
        payload["message"] = message
    return payload


def _is_past(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment <= datetime.now(timezone.utc)


def parse_pin(payload: dict) -> dict:
    """``state`` is pending, authorized, or expired.

    ``auth_token`` is set only when authorized. Do not log the return value.
    """

    token = str(payload.get("authToken") or "").strip()
    if token:
        return {"state": "authorized", "auth_token": token}
    expires = str(payload.get("expiresAt") or "")
    if expires and _is_past(expires):
        return {"state": "expired", "auth_token": ""}
    return {"state": "pending", "auth_token": ""}


class PlexAccount:
    """Calls plex.tv. ``base_url`` is overridden in tests."""

    def __init__(self, client_id: str, timeout: float = 15.0, base_url: str = PLEX_TV):
        self.client_id = client_id or "plex-audiobooks"
        self.timeout = timeout
        self.base_url = (base_url or PLEX_TV).rstrip("/")

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        token: str = "",
        timeout: float | None = None,
    ) -> bytes:
        if token and any(char in token for char in "\r\n"):
            raise PlexError("The Plex token contains characters that are not allowed.")
        url = self.base_url + path
        request = urllib.request.Request(
            url,
            data=data,
            headers=client_headers(self.client_id, token),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout, context=ssl_context()) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            if err.code == 401:
                raise PlexError("Plex rejected the sign-in. Request a new code.", status=401) from None
            if err.code == 404:
                raise PlexError("That sign-in code expired. Request a new one.", status=404) from None
            raise PlexError(f"plex.tv returned HTTP {err.code}.", status=err.code) from None
        except urllib.error.URLError as err:
            raise PlexError(_UNREACHABLE + _reason(err), unreachable=True) from None
        except (TimeoutError, OSError) as err:
            raise PlexError(_UNREACHABLE + _reason(err), unreachable=True) from None

    def _json(self, path: str, *, method: str = "GET", data: bytes | None = None, token: str = "") -> object:
        raw = self._request(path, method=method, data=data, token=token)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise PlexError("plex.tv sent a response this plugin could not read.") from None

    def create_pin(self) -> dict:
        """Start a link-code sign-in. ``strong=false`` is the short code for plex.tv/link."""

        body = urllib.parse.urlencode({
            "strong": "false",
            "X-Plex-Product": PRODUCT_NAME,
            "X-Plex-Client-Identifier": self.client_id,
        }).encode("utf-8")
        payload = self._json("/api/v2/pins", method="POST", data=body)
        if not isinstance(payload, dict):
            raise PlexError("plex.tv did not return a sign-in code.")
        pin_id = str(payload.get("id") or "")
        code = str(payload.get("code") or "")
        if not pin_id or not code:
            raise PlexError("plex.tv did not return a sign-in code.")
        return {"id": pin_id, "code": code}

    def check_pin(self, pin_id: str, code: str) -> dict:
        query = urllib.parse.urlencode({"code": code})
        path = f"/api/v2/pins/{urllib.parse.quote(str(pin_id), safe='')}?{query}"
        try:
            payload = self._json(path)
        except PlexError as err:
            if err.status == 404:
                return {"state": "expired", "auth_token": ""}
            raise
        if not isinstance(payload, dict):
            raise PlexError("plex.tv sent a sign-in response this plugin could not read.")
        return parse_pin(payload)

    def username(self, account_token: str) -> str:
        payload = self._json("/api/v2/user", token=account_token)
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("username") or payload.get("title") or payload.get("friendlyName") or "")

    def list_servers(self, account_token: str) -> list[dict]:
        query = urllib.parse.urlencode({
            "includeHttps": "1",
            "includeRelay": "1",
            "includeIPv6": "1",
        })
        payload = self._json(f"/api/v2/resources?{query}", token=account_token)
        return parse_servers(payload, account_token)
