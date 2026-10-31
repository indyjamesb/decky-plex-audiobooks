from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time

# The plugin directory is on sys.path when Decky loads this file. Inserting it
# again keeps `import plex` working if the loader's path order changes.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import decky

from deck_hid import DeckHidWatcher, migrate_shortcut, normalize_buttons
from player import Player, PlayerError, build_child_env, find_ffplay, find_pulse_socket, set_process_volume
from plex import PlexClient, PlexError, normalize_server_url
from plex_auth import PlexAccount, public_server, sign_in_payload
from progress import (
    AudioTrack,
    book_position,
    choose_resume,
    normalize_book_sort,
    normalize_log_level,
    playback_outcome,
    previous_target,
    reported_position_ms,
    skip_position,
    snap_speed,
    sort_books,
    track_is_complete,
)

VERSION = "0.1.4"
REPORT_INTERVAL_S = 15
_PY_LOG_LEVELS = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

DEFAULTS = {
    "server_url": "",
    "server_name": "",
    "server_id": "",
    "token": "",
    "account_token": "",
    "username": "",
    "auth_method": "",
    "library_key": "",
    "library_title": "",
    "track_progress": None,
    "client_id": "",
    "speed": 1.0,
    "volume": 80,
    "book_sort": "recently_played",
    "play_pause_buttons": ["R4"],
    "play_pause_source": "hidraw",
    "log_level": "info",
}


def _to_thread(func, *args):
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, func, *args)


class Plugin:
    """Quick Access panel and background Plex audiobook player.

    Audio is an ffplay process owned by this backend, so it keeps going when
    the Quick Access Menu closes and a game is in front. The panel only polls
    status while it is open. Progress is reported to Plex from this process.
    """

    def __init__(self):
        self.settings = dict(DEFAULTS)
        self.settings_path = ""
        self.player: Player | None = None
        self.book: dict | None = None
        self.tracks: list[AudioTrack] = []
        self.index = 0
        self.rest_offset_ms = 0
        self.cover = ""
        self.volume = 80
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._running = False
        self._tick_task: asyncio.Task | None = None
        self._last_report = 0.0
        self._align_gen = 0
        self._started_wall = 0.0
        self._cover_cache: dict[str, str] = {}
        self._pending_pin: dict | None = None
        # Full server records, including access tokens. Never returned to the panel.
        self._server_cache: list[dict] = []
        self._frontend_log_times: list[float] = []
        self._hid: DeckHidWatcher | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shortcut_lock = threading.Lock()
        self._last_shortcut_at = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _main(self):
        self._running = True
        self._lock = asyncio.Lock()
        settings_dir = decky.DECKY_PLUGIN_SETTINGS_DIR
        self.settings_path = os.path.join(settings_dir, "settings.json")
        self._load_settings()
        if not self.settings.get("client_id"):
            self.settings["client_id"] = os.urandom(16).hex()
            self._write_settings()
        self.volume = int(self.settings.get("volume") or 80)
        self._apply_log_level()
        log_dir = decky.DECKY_PLUGIN_LOG_DIR
        os.makedirs(log_dir, exist_ok=True)
        env = build_child_env(dict(os.environ), find_pulse_socket())
        self.player = Player(find_ffplay(), os.path.join(log_dir, "ffplay.log"), env)
        self._tick_task = asyncio.create_task(self._tick_loop())
        self._loop = asyncio.get_running_loop()
        self._hid = DeckHidWatcher(on_fire=self._schedule_shortcut, log_debug=self._hid_debug)
        self._hid.set_shortcut(list(self.settings.get("play_pause_buttons") or []))
        self._hid.start()
        decky.logger.info("Plex Audiobooks %s loaded", VERSION)

    async def _unload(self):
        self._running = False
        if self._hid is not None:
            self._hid.stop()
        self._align_gen += 1
        if self._tick_task is not None:
            self._tick_task.cancel()
        try:
            await self.stop_playback()
        except Exception:
            decky.logger.error("Stop during unload failed")
        decky.logger.info("Plex Audiobooks unloaded")

    async def _uninstall(self):
        decky.logger.info("Plex Audiobooks uninstalled")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        try:
            if not os.path.exists(self.settings_path):
                return
            with open(self.settings_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("settings were not an object")
            merged = dict(DEFAULTS)
            for key in DEFAULTS:
                if key in loaded:
                    merged[key] = loaded[key]
            merged["log_level"] = normalize_log_level(merged.get("log_level"))
            merged["play_pause_buttons"] = migrate_shortcut(loaded)
            merged["play_pause_source"] = "hidraw"
            self.settings = merged
        except Exception:
            # The file contains the token. Do not log the parse error or the body.
            decky.logger.error("Could not read settings. Using defaults.")
            self.settings = dict(DEFAULTS)

    def _write_settings(self) -> None:
        directory = os.path.dirname(self.settings_path)
        os.makedirs(directory, exist_ok=True)
        temporary = self.settings_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.settings, handle, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.settings_path)
        os.chmod(self.settings_path, 0o600)

    async def _save_settings(self) -> None:
        try:
            await _to_thread(self._write_settings)
        except Exception:
            decky.logger.error("Could not save settings")

    def _client(self, server_url: str | None = None, token: str | None = None) -> PlexClient:
        return PlexClient(
            server_url if server_url is not None else self.settings.get("server_url") or "",
            token if token is not None else self.settings.get("token") or "",
            self.settings.get("client_id") or "",
        )

    def _configured_message(self) -> str | None:
        if not self.settings.get("server_url") or not self.settings.get("token"):
            return "Add the Plex server in Settings."
        if not self.settings.get("library_key"):
            return "Choose an audiobook library in Settings."
        return None

    def _ensure_client_id(self) -> str:
        if not self.settings.get("client_id"):
            self.settings["client_id"] = os.urandom(16).hex()
            if self.settings_path:
                try:
                    self._write_settings()
                except Exception:
                    decky.logger.error("Could not save settings")
        return str(self.settings["client_id"])

    def _account(self) -> PlexAccount:
        return PlexAccount(self._ensure_client_id())

    async def get_settings(self):
        speed = self._nearest_speed(self.settings.get("speed") or 1)
        signed_in = bool(self.settings.get("account_token") or self.settings.get("token"))
        return {
            "server_url": self.settings.get("server_url") or "",
            "server_name": self.settings.get("server_name") or "",
            "username": self.settings.get("username") or "",
            "signed_in": signed_in,
            "auth_method": self.settings.get("auth_method") or "",
            "has_token": bool(self.settings.get("token")),
            "library_key": self.settings.get("library_key") or "",
            "library_title": self.settings.get("library_title") or "",
            "track_progress": self.settings.get("track_progress"),
            "speed": speed,
            "volume": int(self.settings.get("volume") if self.settings.get("volume") is not None else 80),
            "book_sort": normalize_book_sort(self.settings.get("book_sort")),
            "play_pause_buttons": list(self.settings.get("play_pause_buttons") or []),
            "hidraw_active": bool(self._hid.active) if self._hid is not None else False,
            "log_level": normalize_log_level(self.settings.get("log_level")),
        }

    def _apply_log_level(self) -> None:
        name = normalize_log_level(self.settings.get("log_level"))
        self.settings["log_level"] = name
        decky.logger.setLevel(_PY_LOG_LEVELS[name])

    async def set_log_level(self, level: str):
        self.settings["log_level"] = normalize_log_level(level)
        self._apply_log_level()
        await self._save_settings()
        decky.logger.info("Log level set to %s", self.settings["log_level"])
        return {"success": True, "log_level": self.settings["log_level"]}

    async def log_frontend(self, level: str, message: str):
        """Frontend diagnostic line. Dropped when it is finer than the saved level."""

        name = normalize_log_level(level) if str(level or "").lower() in _PY_LOG_LEVELS else "info"
        if str(level or "").lower() in _PY_LOG_LEVELS:
            name = str(level).lower()
        if not decky.logger.isEnabledFor(_PY_LOG_LEVELS[name]):
            return {"success": True, "logged": False}
        text = " ".join(str(message or "").split())[:500]
        if not text:
            return {"success": True, "logged": False}
        now = time.monotonic()
        recent = [stamp for stamp in self._frontend_log_times if now - stamp < 60]
        self._frontend_log_times = recent
        if len(recent) >= 200 or sum(1 for stamp in recent if now - stamp < 1) >= 20:
            return {"success": True, "logged": False}
        self._frontend_log_times.append(now)
        decky.logger.log(_PY_LOG_LEVELS[name], "frontend: %s", text)
        return {"success": True, "logged": True}

    def _hid_debug(self, message: str) -> None:
        decky.logger.debug("%s", message)

    def _schedule_shortcut(self) -> None:
        loop = self._loop
        if loop is None or not self._running:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.shortcut_toggle("hidraw"), loop)
        except RuntimeError:
            decky.logger.debug("hidraw shortcut skipped; plugin loop is stopped")

    def _claim_shortcut(self) -> bool:
        now = time.monotonic()
        with self._shortcut_lock:
            if now - self._last_shortcut_at < 0.4:
                return False
            self._last_shortcut_at = now
            return True

    async def shortcut_toggle(self, source: str = "frontend"):
        """Play/pause from a controller. Hidraw and the Steam UI share one debounce."""

        if not self._claim_shortcut():
            decky.logger.debug("shortcut suppressed source=%s", source)
            return {"success": True, "suppressed": True}
        result = await self.toggle_playback()
        if result.get("success"):
            decky.logger.info("Shortcut toggled playback (%s)", source)
        else:
            decky.logger.info("Shortcut toggle failed (%s): %s", source, result.get("message") or "failed")
        return result

    async def hidraw_status(self):
        hid = self._hid
        return {
            "success": True,
            "active": bool(hid.active) if hid is not None else False,
            "nodes": list(hid.nodes) if hid is not None else [],
        }

    async def start_shortcut_capture(self):
        hid = self._hid
        if hid is None or not hid.active:
            return {"success": False, "message": "Deck controller is not open yet."}
        state = hid.start_capture()
        return {"success": True, **state}

    async def cancel_shortcut_capture(self):
        if self._hid is not None:
            self._hid.cancel_capture()
        return {"success": True}

    async def shortcut_capture_state(self):
        if self._hid is None:
            return {"success": True, "phase": "idle", "buttons": [], "label": "", "seconds_left": None, "hidraw": False}
        return {"success": True, **self._hid.capture_state()}

    async def set_play_pause_buttons(self, buttons: list):
        cleaned = normalize_buttons(buttons if isinstance(buttons, list) else [])
        self.settings["play_pause_buttons"] = cleaned
        self.settings["play_pause_source"] = "hidraw"
        if self._hid is not None:
            self._hid.set_shortcut(cleaned)
        await self._save_settings()
        decky.logger.info("Play/pause binding set to %s", "+".join(cleaned) or "not set")
        return {"success": True, "play_pause_buttons": cleaned}

    async def save_settings(self, server_url: str, token: str, library_key: str, library_title: str, track_progress):
        url = normalize_server_url(server_url)
        if not url:
            return {"success": False, "message": "Enter the Plex server URL."}
        supplied_token = (token or "").strip()
        if supplied_token:
            if any(char in supplied_token for char in "\r\n"):
                return {"success": False, "message": "The Plex token contains characters that are not allowed."}
            self.settings["token"] = supplied_token
            self.settings["auth_method"] = "manual"
        if not self.settings.get("token"):
            return {"success": False, "message": "Enter a Plex token, or sign in with a link code."}
        self.settings["server_url"] = url
        self.settings["library_key"] = str(library_key or "")
        # An empty title means the panel saved while the library list was not
        # loaded. Keep the name we already stored.
        if library_title:
            self.settings["library_title"] = library_title
        if track_progress is not None:
            self.settings["track_progress"] = bool(track_progress)
        await self._save_settings()
        return {"success": True}

    async def test_connection(self, server_url: str, token: str):
        url = normalize_server_url(server_url) or (self.settings.get("server_url") or "")
        supplied = (token or "").strip()
        tok = supplied or (self.settings.get("token") or "")
        if not url:
            return {"success": False, "message": "Enter the Plex server URL.", "libraries": []}
        if not tok:
            return {
                "success": False,
                "message": "Enter a Plex token. It is stored only on this Deck.",
                "libraries": [],
            }
        client = self._client(url, tok)
        try:
            identity = await _to_thread(client.identity)
            libraries = await _to_thread(client.list_libraries)
            name = identity.get("friendly_name") or "Plex"
            return {
                "success": True,
                "message": f"Connected to {name}.",
                "server_name": name,
                "libraries": libraries,
            }
        except PlexError as err:
            return {"success": False, "message": err.message, "libraries": []}
        except Exception:
            decky.logger.error("Connection test failed")
            return {"success": False, "message": "Couldn't reach the Plex server.", "libraries": []}

    async def start_sign_in(self):
        """Ask plex.tv for a short code the user enters at plex.tv/link."""

        try:
            pin = await _to_thread(self._account().create_pin)
        except PlexError as err:
            return sign_in_payload(success=False, message=err.message)
        except Exception:
            decky.logger.error("Could not start Plex sign-in")
            return sign_in_payload(success=False, message="Can't reach plex.tv to sign in.")
        self._pending_pin = {"id": pin["id"], "code": pin["code"]}
        return sign_in_payload(pending=True, code=pin["code"])

    async def poll_sign_in(self):
        """One poll of the current code. The panel calls this until it is claimed."""

        pending = self._pending_pin
        if not pending:
            if self.settings.get("account_token"):
                listed = await self.list_servers()
                return sign_in_payload(
                    success=bool(listed.get("success")),
                    signed_in=True,
                    username=self.settings.get("username") or "",
                    servers=self._server_cache if listed.get("success") else [],
                    message=listed.get("message") or "",
                )
            return sign_in_payload(success=False, message="Request a sign-in code first.")
        try:
            status = await _to_thread(self._account().check_pin, pending["id"], pending["code"])
        except PlexError as err:
            return sign_in_payload(success=False, pending=True, code=pending["code"], message=err.message)
        except Exception:
            decky.logger.error("Plex sign-in poll failed")
            return sign_in_payload(
                success=False, pending=True, code=pending["code"], message="Can't reach plex.tv to sign in.",
            )
        state = status.get("state")
        if state == "pending":
            return sign_in_payload(pending=True, code=pending["code"])
        if state != "authorized":
            self._pending_pin = None
            return sign_in_payload(
                success=False, expired=True, message="That code expired. Request a new one.",
            )
        account_token = str(status.get("auth_token") or "").strip()
        if not account_token or any(char in account_token for char in "\r\n"):
            self._pending_pin = None
            return sign_in_payload(success=False, message="Plex did not return a usable sign-in.")
        self.settings["account_token"] = account_token
        self.settings["auth_method"] = "link"
        self._pending_pin = None
        self._server_cache = []
        await self._save_settings()
        try:
            username = await _to_thread(self._account().username, account_token)
        except Exception:
            decky.logger.warning("Could not read the Plex account name")
            username = ""
        if username:
            self.settings["username"] = username
            await self._save_settings()
        listed = await self._load_servers(account_token)
        return sign_in_payload(
            success=True,
            signed_in=True,
            username=self.settings.get("username") or "",
            servers=self._server_cache if listed.get("success") else [],
            message="" if listed.get("success") else (listed.get("message") or "Couldn't load your Plex servers."),
        )

    async def list_servers(self):
        token = str(self.settings.get("account_token") or "")
        if not token:
            return {"success": False, "message": "Sign in first.", "servers": []}
        return await self._load_servers(token)

    async def _load_servers(self, account_token: str) -> dict:
        try:
            servers = await _to_thread(self._account().list_servers, account_token)
        except PlexError as err:
            return {"success": False, "message": err.message, "servers": []}
        except Exception:
            decky.logger.error("Could not list Plex servers")
            return {"success": False, "message": "Couldn't load your Plex servers.", "servers": []}
        self._server_cache = servers
        return {"success": True, "servers": [public_server(server) for server in servers]}

    async def select_server(self, client_identifier: str):
        client_identifier = str(client_identifier or "")
        if not self._server_cache:
            token = str(self.settings.get("account_token") or "")
            if not token:
                return {"success": False, "message": "Sign in first."}
            loaded = await self._load_servers(token)
            if not loaded.get("success"):
                return {"success": False, "message": loaded.get("message") or "Couldn't load your Plex servers."}
        match = next(
            (server for server in self._server_cache if server.get("client_identifier") == client_identifier),
            None,
        )
        if match is None:
            return {"success": False, "message": "That server is no longer listed. Refresh the list and try again."}
        server_token = str(match.get("access_token") or self.settings.get("account_token") or "").strip()
        if not server_token or any(char in server_token for char in "\r\n"):
            return {"success": False, "message": "Plex did not provide a token for that server."}
        new_url = str(match.get("uri") or "")
        changed = (
            new_url != (self.settings.get("server_url") or "")
            or (self.settings.get("server_id") or "") != client_identifier
        )
        if changed and self.tracks:
            async with self._lock:
                await self._stop_locked()
                self.book = None
                self.tracks = []
                self.cover = ""
        self.settings["token"] = server_token
        self.settings["server_url"] = new_url
        self.settings["server_name"] = match.get("name") or ""
        self.settings["server_id"] = client_identifier
        self.settings["auth_method"] = "link"
        if changed:
            self.settings["library_key"] = ""
            self.settings["library_title"] = ""
            self.settings["track_progress"] = None
        await self._save_settings()
        decky.logger.info("Selected Plex server %s", match.get("name") or "server")
        return {
            "success": True,
            "server_url": new_url,
            "server_name": match.get("name") or "",
            "local": bool(match.get("local")),
        }

    async def sign_out(self):
        try:
            async with self._lock:
                if self.tracks:
                    await self._stop_locked()
                self.book = None
                self.tracks = []
                self.cover = ""
                self.last_error = None
        except Exception:
            decky.logger.error("Stop during sign-out failed")
        for key in (
            "token", "account_token", "server_url", "server_name", "server_id",
            "username", "library_key", "library_title",
        ):
            self.settings[key] = ""
        self.settings["track_progress"] = None
        self.settings["auth_method"] = ""
        self._pending_pin = None
        self._server_cache = []
        self._align_gen += 1
        await self._save_settings()
        decky.logger.info("Signed out of Plex")
        return {"success": True}

    # ------------------------------------------------------------------
    # Library
    # ------------------------------------------------------------------

    async def set_book_sort(self, sort_id: str):
        self.settings["book_sort"] = normalize_book_sort(sort_id)
        await self._save_settings()
        return {"success": True, "book_sort": self.settings["book_sort"]}

    async def list_books(self, query: str = "", start: int = 0, sort_id: str = ""):
        problem = self._configured_message()
        if problem:
            return {"success": False, "message": problem, "books": [], "total": 0}
        sort_name = normalize_book_sort(sort_id or self.settings.get("book_sort"))
        client = self._client()
        try:
            result = await _to_thread(
                lambda: client.list_books(
                    self.settings["library_key"],
                    query=query or "",
                    start=int(start or 0),
                    sort_id=sort_name,
                )
            )
        except PlexError as err:
            return {"success": False, "message": err.message, "books": [], "total": 0}
        except Exception:
            decky.logger.error("Listing books failed")
            return {"success": False, "message": "Couldn't reach the Plex server.", "books": [], "total": 0}
        books = sort_books(result.get("books") or [], sort_name)
        await self._attach_covers(client, books)
        return {
            "success": True,
            "books": [_public_book(book) for book in books],
            "total": result.get("total") or 0,
            "start": result.get("start") or 0,
            "sort": sort_name,
        }

    async def get_book(self, album_key: str):
        problem = self._configured_message()
        if problem:
            return {"success": False, "message": problem}
        client = self._client()
        try:
            book, tracks = await _to_thread(client.get_book, str(album_key))
        except PlexError as err:
            return {"success": False, "message": err.message}
        except Exception:
            decky.logger.error("Loading a book failed")
            return {"success": False, "message": "Couldn't reach the Plex server."}
        resume = choose_resume(tracks)
        cover = await self._cover_for(client, book)
        position, total = book_position(tracks, resume.index if not resume.finished else 0, 0 if resume.finished else resume.offset_ms)
        return {
            "success": True,
            "book": _public_book({**book, "cover": cover}),
            "tracks": [
                {"title": track.title, "duration_ms": track.duration_ms, "index": index}
                for index, track in enumerate(tracks)
            ],
            "resume_index": resume.index,
            "resume_offset_ms": resume.offset_ms,
            "finished": resume.finished,
            "book_position_ms": 0 if resume.finished else position,
            "book_duration_ms": total,
            "cover": cover,
        }

    async def _attach_covers(self, client: PlexClient, books: list[dict]) -> None:
        semaphore = asyncio.Semaphore(4)

        async def one(book: dict) -> None:
            thumb = book.get("thumb") or ""
            if not thumb:
                book["cover"] = ""
                return
            cached = self._cover_cache.get(thumb)
            if cached is not None:
                book["cover"] = cached
                return
            async with semaphore:
                cached = self._cover_cache.get(thumb)
                if cached is not None:
                    book["cover"] = cached
                    return
                try:
                    data_url = await _to_thread(client.fetch_cover, thumb)
                except Exception:
                    data_url = ""
            if data_url:
                self._remember_cover(thumb, data_url)
            book["cover"] = data_url

        await asyncio.gather(*(one(book) for book in books))

    async def _cover_for(self, client: PlexClient, book: dict) -> str:
        thumb = book.get("thumb") or ""
        if not thumb:
            return ""
        cached = self._cover_cache.get(thumb)
        if cached is not None:
            return cached
        try:
            data_url = await _to_thread(client.fetch_cover, thumb)
        except Exception:
            return ""
        if data_url:
            self._remember_cover(thumb, data_url)
        return data_url

    def _remember_cover(self, thumb: str, data_url: str) -> None:
        self._cover_cache[thumb] = data_url
        if len(self._cover_cache) > 48:
            oldest = next(iter(self._cover_cache))
            self._cover_cache.pop(oldest, None)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    async def play_book(self, album_key: str, track_index: int, offset_ms: int):
        problem = self._configured_message()
        if problem:
            return {"success": False, "message": problem}
        client = self._client()
        try:
            book, tracks = await _to_thread(client.get_book, str(album_key))
        except PlexError as err:
            self.last_error = err.message
            return {"success": False, "message": err.message}
        except Exception:
            decky.logger.error("Loading a book for playback failed")
            self.last_error = "Couldn't reach the Plex server."
            return {"success": False, "message": self.last_error}
        playable = [track for track in tracks if track.part_key]
        if not playable:
            self.last_error = "This audiobook has no playable files."
            return {"success": False, "message": self.last_error}
        explicit = int(track_index) >= 0
        if explicit:
            index = max(0, min(int(track_index), len(playable) - 1))
            offset = max(0, int(offset_ms))
        else:
            resume = choose_resume(playable)
            index = 0 if resume.finished else resume.index
            offset = 0 if resume.finished else resume.offset_ms
        cover = await self._cover_for(client, book)
        async with self._lock:
            self.book = book
            self.tracks = playable
            self.index = index
            self.rest_offset_ms = offset
            self.cover = cover
            self.last_error = None
            await self._open_queue(playable[index].rating_key)
            result = await self._start_track_locked(index, offset)
            if explicit and result.get("success"):
                self._schedule_align(index)
            return result

    async def toggle_playback(self):
        async with self._lock:
            if not self.tracks or self.player is None:
                return {"success": False, "message": "Pick an audiobook first."}
            if self.player.process_alive():
                if self.player.paused:
                    self.player.resume()
                    await self._report_current("playing")
                else:
                    self.player.pause()
                    await self._report_current("paused")
                return {"success": True}
            index = self.index
            offset = self._current_offset()
            # Pressing play at the end of the book starts it over. The resume
            # point in Plex was already written when the last file finished.
            track = self.tracks[index]
            if (
                index == len(self.tracks) - 1
                and track.duration_ms > 0
                and offset >= max(0, track.duration_ms - 3000)
            ):
                index, offset = 0, 0
            return await self._start_track_locked(index, offset)

    async def stop_playback(self):
        async with self._lock:
            return await self._stop_locked()

    async def skip(self, delta_ms: int):
        async with self._lock:
            if not self.tracks:
                return {"success": False, "message": "Nothing is playing."}
            position = self._current_offset()
            new_index, new_offset = skip_position(self.tracks, self.index, position, int(delta_ms))
            if new_index < self.index:
                self._schedule_align(new_index)
            return await self._start_track_locked(new_index, new_offset)

    async def next_chapter(self):
        async with self._lock:
            if not self.tracks:
                return {"success": False, "message": "Nothing is playing."}
            current = self.tracks[self.index]
            await self._report_locked(current, "stopped", current.duration_ms, finishing=True)
            await self._scrobble_locked(current, True)
            current.view_count = max(1, current.view_count)
            current.view_offset_ms = 0
            if self.index + 1 >= len(self.tracks):
                if self.player is not None:
                    self.rest_offset_ms = current.duration_ms
                    self.player.stop()
                return {"success": True, "message": "That's the end of the book."}
            return await self._start_track_locked(self.index + 1, 0)

    async def previous_chapter(self):
        async with self._lock:
            if not self.tracks:
                return {"success": False, "message": "Nothing is playing."}
            target_index, target_offset = previous_target(self.index, self._current_offset())
            if target_index < self.index:
                self._schedule_align(target_index)
            return await self._start_track_locked(target_index, target_offset)

    async def set_speed(self, speed: float):
        nearest = self._nearest_speed(speed)
        async with self._lock:
            self.settings["speed"] = nearest
            await self._save_settings()
            if self.player is not None and self.player.process_alive() and self.tracks:
                paused = self.player.paused
                position = self.player.position_ms()
                result = await self._start_track_locked(self.index, position)
                if paused and result.get("success"):
                    self.player.pause()
                    await self._report_current("paused")
                result["speed"] = nearest
                return result
            return {"success": True, "speed": nearest}

    async def set_volume(self, volume: int):
        volume = max(0, min(100, int(volume)))
        async with self._lock:
            self.volume = volume
            self.settings["volume"] = volume
            await self._save_settings()
            applied = False
            if self.player is not None and self.player.process_alive() and self.player.proc is not None:
                applied = await _to_thread(
                    set_process_volume, self.player.env, self.player.proc.pid, volume
                )
                if not applied and self.tracks:
                    paused = self.player.paused
                    position = self.player.position_ms()
                    result = await self._start_track_locked(self.index, position)
                    if paused and result.get("success"):
                        self.player.pause()
                    applied = bool(result.get("success"))
            return {"success": True, "volume": volume, "applied": applied}

    async def get_status(self):
        async with self._lock:
            return self._status_locked()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _nearest_speed(self, speed: object) -> float:
        return snap_speed(speed)

    def _current_offset(self) -> int:
        if self.player is not None and self.player.active:
            return self.player.position_ms()
        return self.rest_offset_ms

    async def _stop_locked(self) -> dict:
        if not self.tracks or self.player is None:
            return {"success": True}
        position = self._current_offset()
        self.rest_offset_ms = position
        track = self.tracks[self.index]
        self.player.stop()
        await self._report_locked(track, "stopped", position, finishing=False)
        return {"success": True}

    async def _start_track_locked(self, index: int, offset_ms: int) -> dict:
        if self.player is None or not self.tracks:
            return {"success": False, "message": "Nothing is loaded."}
        index = max(0, min(int(index), len(self.tracks) - 1))
        offset_ms = max(0, int(offset_ms))
        track = self.tracks[index]
        if not track.part_key:
            self.last_error = "That chapter has no audio file in Plex."
            return {"success": False, "message": self.last_error}
        self.index = index
        self.rest_offset_ms = offset_ms
        client = self._client()
        try:
            self.player.start(
                url=client.stream_url(track.part_key),
                headers=client.media_headers(),
                offset_ms=offset_ms,
                speed=self._nearest_speed(self.settings.get("speed") or 1),
                volume=self.volume,
                duration_ms=track.duration_ms,
            )
        except PlayerError as err:
            self.last_error = err.message
            return {"success": False, "message": err.message}
        except Exception:
            decky.logger.error("ffplay failed to launch")
            self.last_error = "Couldn't start playback."
            return {"success": False, "message": self.last_error}
        title = (self.book or {}).get("title") or "Audiobook"
        decky.logger.info("Playing %s — %s", title, track.title)
        self._started_wall = time.monotonic()
        await asyncio.sleep(0.6)
        exit_code = self.player.poll()
        if exit_code is not None:
            self.last_error = (
                "Playback stopped immediately. The server may be unreachable, "
                "or this file isn't one ffplay can play."
            )
            log = self.player.read_log([self.settings.get("token") or ""])
            if log:
                decky.logger.error("ffplay: %s", log[:400])
            self.player.stop()
            self.rest_offset_ms = offset_ms
            return {"success": False, "message": self.last_error}
        self.last_error = None
        await self._report_current("playing")
        return {"success": True}

    async def _open_queue(self, rating_key: str) -> None:
        client = self._client()
        try:
            identity = await _to_thread(client.identity)
            await _to_thread(client.open_play_queue, identity.get("machine_identifier") or "", rating_key)
        except Exception:
            decky.logger.info("Plex play queue was not opened; progress still reports over timeline")

    async def _report_current(self, state: str) -> None:
        if not self.tracks:
            return
        await self._report_locked(self.tracks[self.index], state, self._current_offset(), finishing=False)

    async def _report_locked(self, track: AudioTrack, state: str, position_ms: int, *, finishing: bool) -> None:
        position = reported_position_ms(position_ms, state)
        client = self._client()
        try:
            await _to_thread(
                lambda: client.timeline(
                    track.rating_key,
                    state,
                    position,
                    track.duration_ms,
                    finishing=finishing,
                )
            )
            self._last_report = time.monotonic()
            if state in ("playing", "paused", "stopped"):
                track.view_offset_ms = 0 if finishing else position
        except Exception:
            decky.logger.warning("Progress report failed (%s)", state)

    async def _scrobble_locked(self, track: AudioTrack, played: bool) -> None:
        client = self._client()
        try:
            await _to_thread(client.scrobble, track.rating_key, played)
        except Exception:
            decky.logger.warning("Scrobble failed")

    def _schedule_align(self, index: int) -> None:
        """Move Plex's saved resume point to ``index`` without blocking audio.

        Earlier files are marked played. Later files have their saved offset
        cleared, so the furthest ``viewOffset`` is the chapter now playing.
        """

        self._align_gen += 1
        generation = self._align_gen
        snapshot = list(self.tracks)
        asyncio.create_task(self._align_around(generation, snapshot, index))

    async def _align_around(self, generation: int, tracks: list[AudioTrack], index: int) -> None:
        client = self._client()
        for position, track in enumerate(tracks):
            if generation != self._align_gen or not self._running:
                return
            try:
                if position < index and not track_is_complete(track):
                    await _to_thread(client.scrobble, track.rating_key, True)
                    track.view_count = max(1, track.view_count)
                    track.view_offset_ms = 0
                elif position > index and (track.view_offset_ms > 0 or track.view_count > 0):
                    await _to_thread(client.clear_progress, track.rating_key)
                    track.view_offset_ms = 0
                    track.view_count = 0
            except Exception:
                decky.logger.warning("Progress sync for one chapter failed")

    async def _tick_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                decky.logger.error("Playback tick failed")
            await asyncio.sleep(1)

    async def _tick(self) -> None:
        async with self._lock:
            if self.player is None or not self.tracks or not self.player.active:
                return
            exit_code = self.player.poll()
            if exit_code is not None:
                elapsed = time.monotonic() - self._started_wall
                remaining = max(0, self.tracks[self.index].duration_ms - self.player.base_ms)
                outcome = playback_outcome(exit_code, elapsed, remaining, self.player.speed)
                if outcome == "ended":
                    await self._on_track_ended_locked()
                else:
                    await self._on_track_failed_locked()
                return
            if (
                self.player.process_alive()
                and not self.player.paused
                and time.monotonic() - self._last_report >= REPORT_INTERVAL_S
            ):
                await self._report_current("playing")

    async def _on_track_ended_locked(self) -> None:
        if self.player is None or not self.tracks:
            return
        track = self.tracks[self.index]
        self.player.mark_inactive()
        await self._report_locked(track, "stopped", track.duration_ms, finishing=True)
        await self._scrobble_locked(track, True)
        track.view_count = max(1, track.view_count)
        track.view_offset_ms = 0
        if self.index + 1 < len(self.tracks):
            await self._start_track_locked(self.index + 1, 0)
            return
        self.rest_offset_ms = track.duration_ms
        self.last_error = None

    async def _on_track_failed_locked(self) -> None:
        if self.player is None or not self.tracks:
            return
        position = self.player.position_ms()
        self.rest_offset_ms = position
        log = self.player.read_log([self.settings.get("token") or ""])
        self.player.mark_inactive()
        if log:
            decky.logger.error("ffplay: %s", log[:400])
        self.last_error = "Playback stopped unexpectedly. Check that the server is reachable."
        await self._report_locked(self.tracks[self.index], "stopped", position, finishing=False)

    def _status_locked(self) -> dict:
        speed = self._nearest_speed(self.settings.get("speed") or 1)
        base = {
            "has_book": False,
            "playing": False,
            "paused": False,
            "title": "",
            "author": "",
            "cover": "",
            "chapter_title": "",
            "chapter_index": 0,
            "chapter_count": 0,
            "chapter_position_ms": 0,
            "chapter_duration_ms": 0,
            "book_position_ms": 0,
            "book_duration_ms": 0,
            "speed": speed,
            "volume": self.volume,
            "last_error": self.last_error,
            "album_key": "",
            "configured": self._configured_message() is None,
            "library_title": self.settings.get("library_title") or "",
            "track_progress": self.settings.get("track_progress"),
        }
        if not self.tracks or not self.book:
            return base
        offset = self._current_offset()
        position, total = book_position(self.tracks, self.index, offset)
        track = self.tracks[self.index]
        playing = bool(self.player and self.player.process_alive() and not self.player.paused)
        paused = bool(self.player and self.player.paused)
        base.update({
            "has_book": True,
            "playing": playing,
            "paused": paused,
            "title": self.book.get("title") or "",
            "author": self.book.get("author") or "",
            "cover": self.cover or "",
            "chapter_title": track.title,
            "chapter_index": self.index,
            "chapter_count": len(self.tracks),
            "chapter_position_ms": offset,
            "chapter_duration_ms": track.duration_ms,
            "book_position_ms": position,
            "book_duration_ms": total,
            "album_key": self.book.get("rating_key") or "",
        })
        return base


def _public_book(book: dict) -> dict:
    """Book fields the panel can render. No server paths and no token."""

    return {
        "rating_key": book.get("rating_key") or "",
        "title": book.get("title") or "Untitled",
        "author": book.get("author") or "Unknown author",
        "duration_ms": int(book.get("duration_ms") or 0),
        "leaf_count": int(book.get("leaf_count") or 0),
        "viewed_leaf_count": int(book.get("viewed_leaf_count") or 0),
        "year": book.get("year"),
        "cover": book.get("cover") or "",
    }
