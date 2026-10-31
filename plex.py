"""Small Plex Media Server client for audiobook libraries.

Audiobooks live in a Music library (section type ``artist``). An album is a
book, ``parentTitle`` is the author, and tracks are the files. Plex has no
separate audiobook library type.

The token is sent only as the ``X-Plex-Token`` header, never placed in a URL,
so server errors and player logs do not record it. Callers must not log the
header block returned by :meth:`PlexClient.media_headers`.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from plex_auth import PlexError, _reason, client_headers, ssl_context
from progress import AudioTrack, PLEX_SORT, normalize_book_sort, timeline_duration_ms

_IDENTIFIER = "com.plexapp.plugins.library"
_UNREACHABLE = (
    "Can't reach the Plex server. Check the URL and that this Deck is on the same network."
)


def normalize_server_url(url: str) -> str:
    """Accept ``10.0.0.91:32400`` or a full http(s) URL. Drop a trailing slash."""

    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "http://" + cleaned
    return cleaned


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def parse_book(meta: dict) -> dict:
    author = meta.get("parentTitle") or meta.get("originalTitle") or "Unknown author"
    return {
        "rating_key": str(meta.get("ratingKey") or ""),
        "title": meta.get("title") or "Untitled",
        "author": author,
        "thumb": meta.get("thumb") or meta.get("parentThumb") or "",
        "duration_ms": _as_int(meta.get("duration")),
        "leaf_count": _as_int(meta.get("leafCount")),
        "viewed_leaf_count": _as_int(meta.get("viewedLeafCount")),
        "year": meta.get("year"),
        "last_viewed_at": _as_int(meta.get("lastViewedAt")),
        "added_at": _as_int(meta.get("addedAt")),
    }


def parse_track(meta: dict) -> AudioTrack | None:
    media_list = meta.get("Media") or []
    media = media_list[0] if media_list else {}
    parts = media.get("Part") or []
    part = parts[0] if parts else {}
    part_key = part.get("key") or ""
    rating_key = str(meta.get("ratingKey") or "")
    if not part_key or not rating_key:
        return None
    return AudioTrack(
        rating_key=rating_key,
        title=meta.get("title") or "Untitled",
        index=_as_int(meta.get("index")),
        parent_index=_as_int(meta.get("parentIndex")),
        duration_ms=_as_int(meta.get("duration")),
        view_offset_ms=_as_int(meta.get("viewOffset")),
        view_count=_as_int(meta.get("viewCount")),
        part_key=part_key,
    )


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return False


def parse_track_progress_flag(payload: dict | None, text: str) -> bool | None:
    """Read the library's "Store track progress" (``enableTrackOffsets``) flag.

    ``None`` means the preference was not in the response, so the UI should
    not claim it is off.
    """

    if payload:
        settings = (payload.get("MediaContainer") or {}).get("Setting") or []
        for setting in settings:
            if isinstance(setting, dict) and setting.get("id") == "enableTrackOffsets":
                return _truthy(setting.get("value"))
    match = re.search(r'id="enableTrackOffsets"[^>]*value="([^"]*)"', text)
    if not match:
        match = re.search(r'value="([^"]*)"[^>]*id="enableTrackOffsets"', text)
    if not match:
        return None
    return _truthy(match.group(1))


class PlexClient:
    def __init__(self, server_url: str, token: str, client_id: str, timeout: float = 8.0):
        self.server_url = normalize_server_url(server_url)
        self.token = (token or "").strip()
        self.client_id = client_id or "plex-audiobooks"
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return client_headers(self.client_id, self.token)

    def media_headers(self) -> str:
        """Header block for ffplay. Ends with CRLF, as ffmpeg requires.

        Includes the token. Do not log the return value.
        """

        lines = [f"{key}: {value}" for key, value in self._headers().items() if key != "Accept"]
        return "".join(line + "\r\n" for line in lines)

    def stream_url(self, part_key: str) -> str:
        """Direct part URL. The token is not included; ffplay sends it as a header."""

        path = part_key if part_key.startswith("/") else "/" + part_key
        return self.server_url + path

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        timeout: float | None = None,
    ) -> bytes:
        if not self.server_url:
            raise PlexError("Enter the Plex server URL.")
        if not self.token:
            raise PlexError("Enter a Plex token. It is stored only on this Deck.")
        if any(char in self.token for char in "\r\n"):
            raise PlexError("The Plex token contains characters that are not allowed.")
        url = self.server_url + path
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout, context=ssl_context()) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            if err.code == 401:
                raise PlexError("Plex rejected the token.", status=401) from None
            if err.code == 404:
                raise PlexError("Plex could not find that item.", status=404) from None
            raise PlexError(f"Plex returned HTTP {err.code}.", status=err.code) from None
        except urllib.error.URLError as err:
            raise PlexError(_UNREACHABLE + _reason(err), unreachable=True) from None
        except (TimeoutError, OSError) as err:
            raise PlexError(_UNREACHABLE + _reason(err), unreachable=True) from None

    def _get_json(self, path: str, timeout: float | None = None) -> tuple[dict | None, str]:
        raw = self._request(path, timeout=timeout)
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None, text
        if not isinstance(parsed, dict):
            return None, text
        return parsed, text

    def identity(self) -> dict[str, str]:
        _payload, text = self._get_json("/")
        def attr(name: str) -> str:
            match = re.search(rf'{name}="([^"]*)"', text)
            return match.group(1) if match else ""

        return {
            "friendly_name": attr("friendlyName") or "Plex",
            "machine_identifier": attr("machineIdentifier"),
        }

    def list_libraries(self) -> list[dict]:
        payload, _text = self._get_json("/library/sections")
        if payload is None:
            raise PlexError("Plex sent a library list this plugin could not read.")
        libraries = []
        for section in (payload.get("MediaContainer") or {}).get("Directory") or []:
            if not isinstance(section, dict) or section.get("type") != "artist":
                continue
            key = str(section.get("key") or "")
            if not key:
                continue
            libraries.append({
                "key": key,
                "title": section.get("title") or "Library",
                "track_progress": self.track_progress_enabled(key),
            })
        return libraries

    def track_progress_enabled(self, section_key: str) -> bool | None:
        quoted = urllib.parse.quote(str(section_key), safe="")
        try:
            payload, text = self._get_json(f"/library/sections/{quoted}/prefs", timeout=5)
        except PlexError:
            return None
        return parse_track_progress_flag(payload, text)

    def list_books(
        self,
        section_key: str,
        *,
        query: str = "",
        start: int = 0,
        size: int = 30,
        sort_id: str = "recently_played",
    ) -> dict:
        quoted_section = urllib.parse.quote(str(section_key), safe="")
        start = max(0, int(start))
        size = max(1, min(60, int(size)))
        query = (query or "").strip()
        sort_key = PLEX_SORT[normalize_book_sort(sort_id)]
        if query:
            path = (
                f"/library/sections/{quoted_section}/search?type=9"
                f"&query={urllib.parse.quote(query)}&limit={size}&sort={urllib.parse.quote(sort_key)}"
            )
        else:
            path = (
                f"/library/sections/{quoted_section}/all?type=9&sort={urllib.parse.quote(sort_key)}"
                f"&X-Plex-Container-Start={start}&X-Plex-Container-Size={size}"
            )
        payload, _text = self._get_json(path, timeout=20)
        if payload is None:
            raise PlexError("Plex sent a book list this plugin could not read.")
        container = payload.get("MediaContainer") or {}
        books = []
        for item in container.get("Metadata") or []:
            if isinstance(item, dict) and item.get("ratingKey"):
                books.append(parse_book(item))
        total = container.get("totalSize")
        if total is None:
            total = start + len(books)
        return {"books": books, "total": _as_int(total, start + len(books)), "start": start}

    def get_book(self, album_key: str) -> tuple[dict, list[AudioTrack]]:
        quoted = urllib.parse.quote(str(album_key), safe="")
        payload, _text = self._get_json(f"/library/metadata/{quoted}")
        if payload is None:
            raise PlexError("Plex sent book details this plugin could not read.")
        metas = (payload.get("MediaContainer") or {}).get("Metadata") or []
        if not metas or not isinstance(metas[0], dict):
            raise PlexError("Plex could not find that audiobook.")
        book = parse_book(metas[0])
        tracks = self.get_tracks(album_key)
        if tracks and not book["duration_ms"]:
            book["duration_ms"] = sum(track.duration_ms for track in tracks)
        if tracks and not book["leaf_count"]:
            book["leaf_count"] = len(tracks)
        return book, tracks

    def get_tracks(self, album_key: str) -> list[AudioTrack]:
        quoted = urllib.parse.quote(str(album_key), safe="")
        tracks: list[AudioTrack] = []
        start = 0
        page_size = 100
        while start < 5000:
            path = (
                f"/library/metadata/{quoted}/children"
                f"?X-Plex-Container-Start={start}&X-Plex-Container-Size={page_size}"
            )
            payload, _text = self._get_json(path, timeout=20)
            if payload is None:
                raise PlexError("Plex sent chapter details this plugin could not read.")
            container = payload.get("MediaContainer") or {}
            metadata = container.get("Metadata") or []
            if not metadata:
                break
            for item in metadata:
                if isinstance(item, dict):
                    track = parse_track(item)
                    if track is not None:
                        tracks.append(track)
            start += len(metadata)
            total = container.get("totalSize")
            if total is not None and start >= _as_int(total):
                break
            if len(metadata) < page_size:
                break
        tracks.sort(key=lambda track: (track.parent_index, track.index, track.rating_key))
        return tracks

    def timeline(
        self,
        rating_key: str,
        state: str,
        position_ms: int,
        real_duration_ms: int,
        *,
        finishing: bool = False,
        play_queue_item_id: str = "",
    ) -> None:
        """Report playback progress. ``state`` is playing, paused, or stopped.

        ``playbackTime`` is the same millisecond offset as ``time``. Plex
        rejects a unix timestamp in that field. ``playQueueItemID`` is sent
        when a play queue was opened, which is what Dashboard Now Playing uses.
        """

        duration = timeline_duration_ms(real_duration_ms, position_ms, finishing=finishing)
        params = {
            "ratingKey": str(rating_key),
            "key": f"/library/metadata/{rating_key}",
            "identifier": _IDENTIFIER,
            "state": state,
            "time": str(max(0, int(position_ms))),
            "duration": str(duration),
            "playbackTime": str(max(0, int(position_ms))),
            "hasMDE": "1",
        }
        if play_queue_item_id:
            params["playQueueItemID"] = str(play_queue_item_id)
        self._request("/:/timeline?" + urllib.parse.urlencode(params), timeout=5)

    def scrobble(self, rating_key: str, played: bool) -> None:
        """Mark a file played or unplayed.

        The ``key`` is the bare rating key. The ``/library/metadata/...`` form
        is not accepted on this endpoint.
        """

        endpoint = "/:/scrobble" if played else "/:/unscrobble"
        params = urllib.parse.urlencode({
            "key": str(rating_key),
            "identifier": _IDENTIFIER,
        })
        self._request(f"{endpoint}?{params}", timeout=5)

    def clear_progress(self, rating_key: str) -> None:
        """Best-effort reset of a file's played state and saved offset.

        ``time=0`` on the progress API is ignored by Plex. Scrobbling and then
        unscrobbling is what actually clears a saved offset on current servers.
        """

        self.scrobble(rating_key, True)
        self.scrobble(rating_key, False)

    def open_play_queue(self, machine_identifier: str, rating_key: str) -> str:
        """Open a Plex session for this file. Returns ``playQueueItemID`` when Plex sends one."""

        if not machine_identifier or not rating_key:
            raise PlexError("Plex did not identify this server.")
        uri = (
            f"server://{machine_identifier}/{_IDENTIFIER}/library/metadata/{rating_key}"
        )
        params = urllib.parse.urlencode({
            "type": "audio",
            "uri": uri,
            "repeat": "0",
            "own": "1",
            "includeChapters": "1",
        })
        raw = self._request("/playQueues?" + params, method="POST", data=b"", timeout=5)
        text = raw.decode("utf-8", errors="replace")
        match = re.search(r'playQueueItemID["\s:=]+"?(\d+)', text)
        if match is None:
            match = re.search(r'playQueueSelectedItemID["\s:=]+"?(\d+)', text)
        return match.group(1) if match else ""

    def fetch_cover(self, thumb_path: str, size: int = 180) -> str:
        if not thumb_path:
            return ""
        encoded = urllib.parse.quote(thumb_path, safe="")
        path = f"/photo/:/transcode?width={size}&height={size}&minSize=1&url={encoded}"
        raw = self._request(path, timeout=12)
        if not raw or len(raw) > 500_000:
            return ""
        import base64

        encoded_image = base64.b64encode(raw).decode("ascii")
        return f"data:image/jpeg;base64,{encoded_image}"
