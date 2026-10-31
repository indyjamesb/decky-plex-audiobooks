"""Audiobook progress math.

Plex stores audiobooks as music: an album is a book and each file is a track.
``viewOffset`` is milliseconds into that one file, not into the whole book.
The resume point is therefore the furthest track that still has a saved
offset, plus the durations of the tracks before it. A prefix of files Plex
has marked played (view count set and the offset cleared) is skipped, which
is how a multi-file book continues at the next file.

That layout matches the approach in Music Assistant's Plex provider
(Apache-2.0, music-assistant/server PR 3748). The functions here are original.

Chronicle (GPL-3.0) was only used to confirm a public Plex API fact: timeline
``time`` and ``playbackTime`` are progress in milliseconds. No Chronicle code
is copied. See NOTICE.
"""

from __future__ import annotations

from dataclasses import dataclass


# A file is finished when playback is within this many milliseconds of its end.
# Short files are not treated as finished just because they are shorter than
# the window; see track_is_complete.
_END_SLOP_MS = 3000

# After a scrobble, Plex often clears viewOffset. A few seconds still counts
# as "cleared" so a 1ms anchor written on the next file is not required here.
_CLEARED_OFFSET_MS = 5000

# Pressing previous restarts the current file unless we are still in this
# opening window, in which case it goes to the previous file.
RESTART_CHAPTER_AFTER_MS = 3000


@dataclass
class AudioTrack:
    """One Plex track (a file, or one chapter if Plex split the book that way)."""

    rating_key: str
    title: str
    index: int = 0
    parent_index: int = 0
    duration_ms: int = 0
    view_offset_ms: int = 0
    view_count: int = 0
    part_key: str = ""

    def __post_init__(self) -> None:
        self.rating_key = str(self.rating_key or "")
        self.title = self.title or "Untitled"
        self.index = int(self.index or 0)
        self.parent_index = int(self.parent_index or 0)
        self.duration_ms = max(0, int(self.duration_ms or 0))
        self.view_offset_ms = max(0, int(self.view_offset_ms or 0))
        self.view_count = max(0, int(self.view_count or 0))
        self.part_key = self.part_key or ""


@dataclass(frozen=True)
class Resume:
    """Where to start a book. ``finished`` means every file has been heard."""

    index: int
    offset_ms: int
    finished: bool


def track_is_complete(track: AudioTrack) -> bool:
    """Whether Plex's saved state says this file has been heard through."""

    duration = track.duration_ms
    offset = track.view_offset_ms
    if duration > 0 and offset >= duration:
        return True
    if duration > _END_SLOP_MS and offset >= duration - _END_SLOP_MS:
        return True
    if track.view_count > 0 and offset < _CLEARED_OFFSET_MS:
        return True
    return False


def choose_resume(tracks: list[AudioTrack]) -> Resume:
    """Pick a file index and offset from Plex's per-track progress.

    Two signals are combined, and the one further into the book wins:

    * The first file after a leading run of completed files. This catches a
      multi-file book where finished files were scrobbled and the current
      file has not been started yet (offset still 0).
    * The last file whose ``viewOffset`` is still non-zero. This catches a
      pause in the middle of a file, including a single m4b.
    """

    if not tracks:
        return Resume(0, 0, True)

    def score(index: int, offset: int) -> int:
        return sum(item.duration_ms for item in tracks[:index]) + max(0, offset)

    candidates: list[tuple[int, int]] = []

    prefix = 0
    while prefix < len(tracks) and track_is_complete(tracks[prefix]):
        prefix += 1
    if prefix < len(tracks):
        offset = tracks[prefix].view_offset_ms
        duration = tracks[prefix].duration_ms
        if duration > 0:
            offset = min(offset, duration - 1)
        candidates.append((prefix, max(0, offset)))

    last_with_offset: int | None = None
    for index, track in enumerate(tracks):
        if track.view_offset_ms > 0:
            last_with_offset = index
    if last_with_offset is not None:
        track = tracks[last_with_offset]
        if track_is_complete(track):
            if last_with_offset + 1 < len(tracks):
                candidates.append((last_with_offset + 1, 0))
        else:
            offset = track.view_offset_ms
            if track.duration_ms > 0:
                offset = min(offset, track.duration_ms - 1)
            candidates.append((last_with_offset, max(0, offset)))

    if prefix >= len(tracks) or not candidates:
        return Resume(0, 0, True)

    best_index, best_offset = max(
        candidates,
        key=lambda item: (score(item[0], item[1]), item[0]),
    )
    return Resume(best_index, best_offset, False)


def book_position(tracks: list[AudioTrack], index: int, offset_ms: int) -> tuple[int, int]:
    """Return ``(position_ms, duration_ms)`` for the whole book."""

    total = sum(track.duration_ms for track in tracks)
    if not tracks:
        return 0, 0
    index = max(0, min(index, len(tracks) - 1))
    before = sum(track.duration_ms for track in tracks[:index])
    offset = max(0, offset_ms)
    duration = tracks[index].duration_ms
    if duration > 0:
        offset = min(offset, duration)
    position = before + offset
    if total > 0:
        position = min(position, total)
    return position, total


def locate(tracks: list[AudioTrack], position_ms: int) -> tuple[int, int]:
    """Map a book position in milliseconds to ``(track_index, offset_ms)``."""

    if not tracks:
        return 0, 0
    remaining = max(0, int(position_ms))
    for index, track in enumerate(tracks):
        duration = track.duration_ms
        last = index == len(tracks) - 1
        if duration <= 0:
            if last:
                return index, 0
            continue
        if remaining < duration or last:
            if last:
                return index, min(remaining, duration)
            return index, remaining
        remaining -= duration
    return len(tracks) - 1, 0


def skip_position(
    tracks: list[AudioTrack],
    index: int,
    offset_ms: int,
    delta_ms: int,
) -> tuple[int, int]:
    """Move ``delta_ms`` through the book, crossing file boundaries."""

    position, total = book_position(tracks, index, offset_ms)
    if total <= 0:
        return index, max(0, offset_ms + int(delta_ms))
    target = position + int(delta_ms)
    target = max(0, min(target, max(0, total - 1)))
    return locate(tracks, target)


def previous_target(index: int, offset_ms: int) -> tuple[int, int]:
    """Previous-chapter target. Near the start of a file, step back one file."""

    if index <= 0 or offset_ms > RESTART_CHAPTER_AFTER_MS:
        return max(0, index), 0
    return index - 1, 0


def timeline_duration_ms(real_duration_ms: int, position_ms: int, *, finishing: bool) -> int:
    """Duration query value for ``/:/timeline``.

    Plex marks an item watched at about 90% of the duration included in the
    timeline request. A long m4b would flip to "played" and lose its resume
    point well before the end. While the file is still in progress, report a
    longer duration so that threshold stays past the real end. When the file
    actually finishes, report the real duration and scrobble it explicitly.
    """

    real = max(0, int(real_duration_ms))
    if finishing:
        return real
    position = max(0, int(position_ms))
    return max(real * 2, position + real + 1)


def reported_position_ms(position_ms: int, state: str) -> int:
    """Position sent to Plex.

    A playing or paused report of 0 is easy for Plex to drop. One millisecond
    keeps the file as the resume point without an audible seek.
    """

    position = max(0, int(position_ms))
    if state in ("playing", "paused") and position <= 0:
        return 1
    return position


def compute_position_ms(
    base_ms: int,
    started_at: float | None,
    now: float,
    paused_total: float,
    pause_started: float | None,
    speed: float,
    duration_ms: int,
) -> int:
    """Estimate the file position from the clock.

    ffplay has no query socket. Elapsed wall time, minus time spent paused,
    times playback speed, is the position inside the file. ``base_ms`` is the
    offset ffplay was started at.
    """

    if started_at is None:
        position = base_ms
    else:
        end = pause_started if pause_started is not None else now
        elapsed = max(0.0, end - started_at - paused_total)
        rate = speed if speed > 0 else 1.0
        position = int(base_ms + elapsed * rate * 1000)
    position = max(0, position)
    if duration_ms > 0:
        position = min(position, duration_ms)
    return position


BOOK_SORTS = ("recently_played", "title", "author", "recently_added")

PLEX_SORT = {
    "recently_played": "lastViewedAt:desc",
    "title": "titleSort:asc",
    "author": "artist.titleSort:asc",
    "recently_added": "addedAt:desc",
}


def normalize_book_sort(sort_id: object) -> str:
    text = str(sort_id or "")
    return text if text in BOOK_SORTS else "recently_played"


def sort_books(books: list[dict], sort_id: object) -> list[dict]:
    """Order a page of albums.

    Recently played uses ``lastViewedAt``, then ``addedAt``, then title, so a
    book Plex has never opened still has a stable place.
    """

    chosen = normalize_book_sort(sort_id)

    def title_key(book: dict) -> str:
        return str(book.get("title") or "").lower()

    def author_key(book: dict) -> str:
        return str(book.get("author") or "").lower()

    def viewed(book: dict) -> int:
        return int(book.get("last_viewed_at") or 0)

    def added(book: dict) -> int:
        return int(book.get("added_at") or 0)

    if chosen == "title":
        return sorted(books, key=title_key)
    if chosen == "author":
        return sorted(books, key=lambda book: (author_key(book), title_key(book)))
    if chosen == "recently_added":
        return sorted(books, key=lambda book: (-added(book), title_key(book)))
    return sorted(books, key=lambda book: (-viewed(book), -added(book), title_key(book)))


def snap_speed(speed: object) -> float:
    """Nearest 0.05 step from 0.50 through 3.00. 1.2 lands on 1.2 exactly."""

    try:
        value = float(speed)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if value != value:
        return 1.0
    steps = round((value - 0.5) / 0.05)
    steps = max(0, min(50, steps))
    return round(0.5 + steps * 0.05, 2)


def atempo_filters(speed: float) -> list[str]:
    """ffmpeg ``atempo`` chain for a playback rate. Empty at 1x.

    Each ``atempo`` stage is kept between 0.5 and 2.0, where the filter is
    defined to work. 2.5x is ``atempo=2.0,atempo=1.25``.
    """

    rate = float(speed)
    if abs(rate - 1.0) < 0.01 or rate <= 0:
        return []
    parts: list[str] = []
    remaining = rate
    guard = 0
    while remaining > 2.0 and guard < 8:
        parts.append("atempo=2.0")
        remaining /= 2.0
        guard += 1
    guard = 0
    while remaining < 0.5 and guard < 8:
        parts.append("atempo=0.5")
        remaining /= 0.5
        guard += 1
    parts.append(f"atempo={remaining:.4f}")
    return parts


def playback_outcome(
    exit_code: int | None,
    elapsed_wall_s: float,
    remaining_ms: int,
    speed: float,
) -> str:
    """Classify an ffplay exit as ``ended`` or ``failed``.

    A clean exit after most of the remaining audio is the file finishing.
    An immediate exit on a long file is a failure (bad URL, missing codec,
    audio device). Returns ``ended`` or ``failed``.
    """

    rate = speed if speed > 0 else 1.0
    expected = max(0.0, remaining_ms / 1000.0) / rate
    if elapsed_wall_s < 0.8 and expected > 3:
        return "failed"
    if exit_code in (0, None):
        return "ended"
    if elapsed_wall_s >= max(0.8, expected * 0.85):
        return "ended"
    return "failed"
