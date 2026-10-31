import {
  ButtonItem,
  DialogButton,
  Focusable,
  PanelSection,
  PanelSectionRow,
  DropdownItem,
  SliderField,
  TextField,
  ToggleField,
} from "@decky/ui";
import { useEffect, useRef, useState } from "react";

import {
  BookDetail,
  BookSummary,
  LibraryInfo,
  PlaybackStatus,
  PlexServerChoice,
  getBook,
  getSettings,
  getStatus,
  listBooks,
  listServers,
  setBookSort,
  setLogLevel,
  cancelShortcutCapture,
  hidrawStatus,
  setPlayPauseButtons,
  shortcutCaptureState,
  startShortcutCapture,
  nextChapter,
  playBook,
  pollSignIn,
  previousChapter,
  saveSettings,
  selectServer,
  setAutoLowerGame,
  setDuckLevel,
  setGameVolume,
  setSpeed,
  setVolume,
  signOut,
  skip,
  startSignIn,
  stopPlayback,
  testConnection,
  togglePlayback,
} from "./api";
import {
  bindingLabel,
  cancelProgramming,
  captureSnapshot,
  replayRegistrationLog,
  setShortcutButtons,
  startProgramming,
} from "./controllerShortcut";
import { setClientLogLevel } from "./logClient";
import { BOOK_SORTS, BookSortId, formatSpeed, formatTime, snapSpeed, stepSpeed } from "./format";

type View = "player" | "library" | "book" | "settings";

function percent(position: number, duration: number): number {
  if (!duration || duration <= 0) return 0;
  return Math.max(0, Math.min(100, (position / duration) * 100));
}

function ErrorText({ text }: { text: string }) {
  if (!text) return null;
  return (
    <PanelSectionRow>
      <div style={{ color: "#ffb4ab", whiteSpace: "normal", fontSize: "0.9em" }}>{text}</div>
    </PanelSectionRow>
  );
}

function ProgressBar({ position, duration }: { position: number; duration: number }) {
  return (
    <div style={{ height: 6, background: "rgba(255,255,255,0.15)", borderRadius: 3, overflow: "hidden" }}>
      <div style={{ width: `${percent(position, duration)}%`, height: "100%", background: "#e5a00d" }} />
    </div>
  );
}

function Cover({ src, title }: { src: string; title: string }) {
  if (!src) {
    return (
      <div
        style={{
          width: 96,
          height: 96,
          borderRadius: 4,
          background: "rgba(255,255,255,0.08)",
          flex: "0 0 auto",
        }}
      />
    );
  }
  return (
    <img
      alt={title}
      src={src}
      style={{ width: 96, height: 96, objectFit: "cover", borderRadius: 4, flex: "0 0 auto" }}
    />
  );
}

export function App() {
  const [view, setView] = useState<View>("player");
  const [status, setStatus] = useState<PlaybackStatus | null>(null);
  const [pageError, setPageError] = useState("");
  const [actionError, setActionError] = useState("");
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const busy = useRef(false);

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const next = await getStatus();
        if (!stop) setStatus(next);
      } catch {
        if (!stop) setPageError("The plugin backend is not responding.");
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    let stop = false;
    void (async () => {
      try {
        const settings = await getSettings();
        if (stop) return;
        setSettingsLoaded(true);
        if (!settings.has_token || !settings.library_key) setView("settings");
      } catch {
        if (!stop) setPageError("The plugin backend is not responding.");
      }
    })();
    return () => {
      stop = true;
    };
  }, []);

  async function run(action: () => Promise<{ success: boolean; message?: string }>) {
    if (busy.current) return;
    busy.current = true;
    try {
      const result = await action();
      setActionError(result.success ? "" : result.message || "That didn't work.");
      setStatus(await getStatus());
    } catch {
      setActionError("Couldn't reach the plugin backend.");
    } finally {
      busy.current = false;
    }
  }

  const shownError = actionError || pageError || status?.last_error || "";

  return (
    <>
      {view === "player" && (
        <PlayerView
          status={status}
          error={shownError}
          onLibrary={() => setView("library")}
          onSettings={() => setView("settings")}
          onRun={run}
        />
      )}
      {view === "library" && (
        <LibraryView
          onBack={() => setView("player")}
          onSelectBook={() => setView("player")}
        />
      )}
      {view === "settings" && (
        <SettingsView
          ready={settingsLoaded}
          onBack={() => setView(status?.configured ? "player" : "settings")}
          onSaved={() => setView("library")}
        />
      )}
    </>
  );
}

function PlayerView({
  status,
  error,
  onLibrary,
  onSettings,
  onRun,
}: {
  status: PlaybackStatus | null;
  error: string;
  onLibrary: () => void;
  onSettings: () => void;
  onRun: (action: () => Promise<{ success: boolean; message?: string }>) => Promise<void>;
}) {
  const [volume, setLocalVolume] = useState<number | null>(null);
  const [gameVolume, setLocalGameVolume] = useState<number | null>(null);
  const [duckLevel, setLocalDuck] = useState<number | null>(null);
  const [autoLower, setAutoLower] = useState<boolean | null>(null);
  const [localSpeed, setLocalSpeed] = useState<number | null>(null);
  const volumeTimer = useRef<number | null>(null);
  const gameTimer = useRef<number | null>(null);
  const duckTimer = useRef<number | null>(null);
  const speedTimer = useRef<number | null>(null);
  const shownVolume = volume !== null ? volume : status?.volume ?? 80;
  const shownGame = gameVolume !== null ? gameVolume : status?.game_volume ?? 100;
  const shownDuck = duckLevel !== null ? duckLevel : status?.duck_level ?? 30;
  const lowerGame = autoLower !== null ? autoLower : status?.auto_lower_game !== false;

  useEffect(() => {
    return () => {
      if (volumeTimer.current !== null) window.clearTimeout(volumeTimer.current);
    };
  }, []);

  const book = status?.has_book ? status : null;
  const playing = Boolean(status?.playing);
  const speed = status?.speed || 1;
  const shownSpeed = localSpeed !== null ? localSpeed : snapSpeed(speed);

  return (
    <>
      <PanelSection title="Now playing">
        {!book && (
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal" }}>
              Nothing is loaded. Pick a book from your Plex library. Playback keeps going after you close this menu.
            </div>
          </PanelSectionRow>
        )}
        {book && (
          <>
            <PanelSectionRow>
              <div style={{ display: "flex", gap: "10px", width: "100%" }}>
                <Cover src={book.cover} title={book.title} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, whiteSpace: "normal" }}>{book.title}</div>
                  <div style={{ opacity: 0.8, whiteSpace: "normal" }}>{book.author}</div>
                  <div style={{ opacity: 0.7, marginTop: 4, whiteSpace: "normal" }}>
                    {status?.paused ? "Paused · " : ""}
                    {book.chapter_count > 1
                      ? `File ${book.chapter_index + 1} of ${book.chapter_count}`
                      : "Single file"}
                    {book.chapter_title ? ` · ${book.chapter_title}` : ""}
                  </div>
                </div>
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <div style={{ width: "100%" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85em" }}>
                  <span>Book</span>
                  <span>
                    {formatTime(book.book_position_ms)} / {formatTime(book.book_duration_ms)}
                  </span>
                </div>
                <ProgressBar position={book.book_position_ms} duration={book.book_duration_ms} />
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <div style={{ width: "100%" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85em" }}>
                  <span>Chapter</span>
                  <span>
                    {formatTime(book.chapter_position_ms)} / {formatTime(book.chapter_duration_ms)}
                  </span>
                </div>
                <ProgressBar position={book.chapter_position_ms} duration={book.chapter_duration_ms} />
              </div>
            </PanelSectionRow>
          </>
        )}
        <PanelSectionRow>
          <Focusable style={{ display: "flex", gap: "4px", width: "100%" }}>
            <DialogButton
              focusable={true}
              style={{ flex: 1, minWidth: 0 }}
              onClick={() => void onRun(() => previousChapter())}
              disabled={!book}
            >
              Prev
            </DialogButton>
            <DialogButton
              focusable={true}
              style={{ flex: 1, minWidth: 0 }}
              onClick={() => void onRun(() => skip(-30000))}
              disabled={!book}
            >
              -30s
            </DialogButton>
            <DialogButton
              focusable={true}
              style={{ flex: 1, minWidth: 0 }}
              onClick={() => void onRun(() => togglePlayback())}
              disabled={!book}
            >
              {playing ? "Pause" : "Play"}
            </DialogButton>
            <DialogButton
              focusable={true}
              style={{ flex: 1, minWidth: 0 }}
              onClick={() => void onRun(() => skip(30000))}
              disabled={!book}
            >
              +30s
            </DialogButton>
            <DialogButton
              focusable={true}
              style={{ flex: 1, minWidth: 0 }}
              onClick={() => void onRun(() => nextChapter())}
              disabled={!book}
            >
              Next
            </DialogButton>
          </Focusable>
        </PanelSectionRow>
        <PanelSectionRow>
          <Focusable style={{ display: "flex", gap: "4px", width: "100%", alignItems: "center" }}>
            <DialogButton
              style={{ flex: 1 }}
              onClick={() => {
                const next = stepSpeed(shownSpeed, -1);
                setLocalSpeed(next);
                void onRun(() => setSpeed(next));
              }}
            >
              −0.05
            </DialogButton>
            <div style={{ flex: 1, textAlign: "center", fontWeight: 700 }}>{formatSpeed(shownSpeed)}</div>
            <DialogButton
              style={{ flex: 1 }}
              onClick={() => {
                const next = stepSpeed(shownSpeed, 1);
                setLocalSpeed(next);
                void onRun(() => setSpeed(next));
              }}
            >
              +0.05
            </DialogButton>
          </Focusable>
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Speed"
            description="0.50x to 3.00x in steps of 0.05."
            value={shownSpeed}
            min={0.5}
            max={3}
            step={0.05}
            showValue={true}
            onChange={(value) => {
              const next = snapSpeed(value);
              setLocalSpeed(next);
              if (speedTimer.current !== null) window.clearTimeout(speedTimer.current);
              speedTimer.current = window.setTimeout(() => {
                speedTimer.current = null;
                void onRun(() => setSpeed(next));
              }, 300);
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void onRun(() => stopPlayback())} disabled={!book}>
            Stop and save progress
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Book volume"
            value={shownVolume}
            min={0}
            max={100}
            step={1}
            showValue={true}
            onChange={(value) => {
              setLocalVolume(value);
              if (volumeTimer.current !== null) window.clearTimeout(volumeTimer.current);
              volumeTimer.current = window.setTimeout(() => {
                volumeTimer.current = null;
                void onRun(() => setVolume(value));
              }, 300);
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Game volume"
            value={shownGame}
            min={0}
            max={100}
            step={1}
            showValue={true}
            onChange={(value) => {
              setLocalGameVolume(value);
              if (gameTimer.current !== null) window.clearTimeout(gameTimer.current);
              gameTimer.current = window.setTimeout(() => {
                gameTimer.current = null;
                void onRun(() => setGameVolume(value));
              }, 300);
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Lower game while book plays"
            checked={lowerGame}
            onChange={(checked) => {
              setAutoLower(checked);
              void onRun(() => setAutoLowerGame(checked));
            }}
          />
        </PanelSectionRow>
        {lowerGame && (
          <PanelSectionRow>
            <SliderField
              label="Lowered game volume"
              value={shownDuck}
              min={0}
              max={100}
              step={1}
              showValue={true}
              onChange={(value) => {
                setLocalDuck(value);
                if (duckTimer.current !== null) window.clearTimeout(duckTimer.current);
                duckTimer.current = window.setTimeout(() => {
                  duckTimer.current = null;
                  void onRun(() => setDuckLevel(value));
                }, 300);
              }}
            />
          </PanelSectionRow>
        )}
        {status?.track_progress === false && (
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal", fontSize: "0.85em" }}>
              Resume in Plex: Off. Plex’s “Store track progress” is off for this library, so Plex will not keep your
              spot. Turn it on in Plex web: Manage Library, Edit, Advanced. This plugin cannot change that setting.
            </div>
          </PanelSectionRow>
        )}
        <ErrorText text={error} />
      </PanelSection>
      <PlayPauseTrigger />
      <PanelSection title="Browse">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onLibrary}>
            {status?.library_title ? status.library_title : "Audiobook library"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onSettings}>
            Server settings
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

function LibraryView({
  onBack,
  onSelectBook,
}: {
  onBack: () => void;
  onSelectBook: () => void;
}) {
  const [query, setQuery] = useState("");
  const [sortId, setSortId] = useState<BookSortId>("recently_played");
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<BookDetail | null>(null);

  async function load(search: string, start: number, append: boolean, sort: BookSortId = sortId) {
    setLoading(true);
    setError("");
    try {
      const result = await listBooks(search, start, sort);
      if (!result.success) {
        setError(result.message || "Couldn't load the library.");
        if (!append) setBooks([]);
      } else {
        setBooks((current) => (append ? current.concat(result.books) : result.books));
        setTotal(result.total || 0);
      }
    } catch {
      setError("Can't reach the Plex server, or the plugin backend is not responding.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let stop = false;
    void getSettings()
      .then((settings) => {
        if (stop) return;
        const next = BOOK_SORTS.some((item) => item.id === settings.book_sort)
          ? (settings.book_sort as BookSortId)
          : "recently_played";
        setSortId(next);
        return load("", 0, false, next);
      })
      .catch(() => {
        if (!stop) void load("", 0, false, "recently_played");
      });
    return () => {
      stop = true;
    };
  }, []);

  if (selected?.book) {
    return (
      <BookView
        detail={selected}
        onBack={() => setSelected(null)}
        onStarted={onSelectBook}
      />
    );
  }

  return (
    <PanelSection title="Library">
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onBack}>
          Back
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() => void load(query.trim(), 0, false)}
        >
          Search
        </ButtonItem>
      </PanelSectionRow>
      {BOOK_SORTS.map((item) => (
        <PanelSectionRow key={item.id}>
          <ButtonItem
            layout="below"
            onClick={() => {
              setSortId(item.id);
              void setBookSort(item.id);
              void load(query.trim(), 0, false, item.id);
            }}
          >
            <div style={{ whiteSpace: "normal", textAlign: "left" }}>
              <div style={{ fontWeight: sortId === item.id ? 700 : 400 }}>{item.label}</div>
              <div style={{ opacity: 0.75, fontSize: "0.85em" }}>
                {sortId === item.id ? "Selected sort" : "Tap to sort"}
              </div>
            </div>
          </ButtonItem>
        </PanelSectionRow>
      ))}
      {loading && books.length === 0 && (
        <PanelSectionRow>
          <div>Loading books…</div>
        </PanelSectionRow>
      )}
      <ErrorText text={error} />
      {books.map((book) => (
        <PanelSectionRow key={book.rating_key}>
          <ButtonItem
            layout="below"
            onClick={() => {
              setError("");
              setLoading(true);
              void getBook(book.rating_key)
                .then((detail) => {
                  if (!detail.success || !detail.book) {
                    setError(detail.message || "Couldn't open that book.");
                    return;
                  }
                  setSelected(detail);
                })
                .catch(() => setError("Can't reach the Plex server."))
                .finally(() => setLoading(false));
            }}
          >
            <div style={{ display: "flex", gap: "8px", width: "100%", textAlign: "left" }}>
              {book.cover ? (
                <img
                  alt=""
                  src={book.cover}
                  style={{ width: 44, height: 44, objectFit: "cover", borderRadius: 2, flex: "0 0 auto" }}
                />
              ) : null}
              <div style={{ whiteSpace: "normal", minWidth: 0 }}>
                <div>{book.title}</div>
                <div style={{ opacity: 0.75, fontSize: "0.85em" }}>
                  {book.author}
                  {book.leaf_count ? ` · ${book.viewed_leaf_count}/${book.leaf_count} files` : ""}
                </div>
              </div>
            </div>
          </ButtonItem>
        </PanelSectionRow>
      ))}
      {!loading && books.length === 0 && !error && (
        <PanelSectionRow>
          <div style={{ whiteSpace: "normal" }}>No audiobooks in this library.</div>
        </PanelSectionRow>
      )}
      {books.length < total && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void load(query.trim(), books.length, true)} disabled={loading}>
            {loading ? "Loading…" : "Load more"}
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

function BookView({
  detail,
  onBack,
  onStarted,
}: {
  detail: BookDetail;
  onBack: () => void;
  onStarted: () => void;
}) {
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [chaptersOpen, setChaptersOpen] = useState(false);
  const book = detail.book;
  if (!book) return null;
  let cursor = 0;
  const chapters = detail.tracks.map((chapter) => {
    const start = cursor;
    cursor += chapter.duration_ms || 0;
    return { ...chapter, start };
  });

  async function start(trackIndex: number, offsetMs: number) {
    setStarting(true);
    setError("");
    try {
      const result = await playBook(book!.rating_key, trackIndex, offsetMs);
      if (!result.success) {
        setError(result.message || "Couldn't start playback.");
        return;
      }
      onStarted();
    } catch {
      setError("Can't reach your Plex server.");
    } finally {
      setStarting(false);
    }
  }

  return (
    <PanelSection title={book.title}>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onBack}>
          Back
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ display: "flex", gap: "10px", width: "100%" }}>
          <Cover src={detail.cover || book.cover} title={book.title} />
          <div style={{ whiteSpace: "normal" }}>
            <div>{book.author}</div>
            <div style={{ opacity: 0.75, marginTop: 4 }}>
              {formatTime(detail.book_duration_ms)}
              {detail.finished ? " · Finished" : ""}
            </div>
          </div>
        </div>
      </PanelSectionRow>
      {chaptersOpen ? (
        <>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => setChaptersOpen(false)}>
              Back
            </ButtonItem>
          </PanelSectionRow>
          {chapters.map((chapter) => (
            <PanelSectionRow key={chapter.index}>
              <ButtonItem layout="below" disabled={starting} onClick={() => void start(chapter.index, 0)}>
                <div style={{ whiteSpace: "normal", textAlign: "left" }}>
                  {chapter.title || `Chapter ${chapter.index + 1}`}
                  <span style={{ opacity: 0.7 }}>{` · ${formatTime(chapter.start)}`}</span>
                </div>
              </ButtonItem>
            </PanelSectionRow>
          ))}
        </>
      ) : (
        <>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={starting} onClick={() => void start(-1, 0)}>
              {starting ? "Starting…" : "Resume"}
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={starting} onClick={() => setChaptersOpen(true)}>
              Chapters
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
      <ErrorText text={error} />
    </PanelSection>
  );
}

function captureMessage(phase: string, buttons: string[], secondsLeft: number | null): string {
  if (phase === "wait_release") return "Release every button.";
  if (phase === "listen") {
    const label = buttons.length ? bindingLabel(buttons) : "Press the shortcut";
    if (secondsLeft == null) return `${label}.`;
    return `${label} · ${secondsLeft.toFixed(1)}s`;
  }
  if (phase === "done") return buttons.length ? bindingLabel(buttons) : "Not set";
  if (phase === "timeout") return "Nothing pressed. Cancelled.";
  return "Cancelled.";
}

function PlayPauseTrigger() {
  const [buttons, setButtons] = useState<string[]>([]);
  const [recording, setRecording] = useState(false);
  const [message, setMessage] = useState("");
  const useHidraw = useRef(false);
  const startedAt = useRef(0);

  useEffect(() => {
    let stop = false;
    void getSettings()
      .then((settings) => {
        if (stop) return;
        const next = settings.play_pause_buttons ?? ["R4"];
        setButtons(next);
        setShortcutButtons(next);
        setClientLogLevel(settings.log_level || "info");
      })
      .catch(() => undefined);
    return () => {
      stop = true;
      cancelProgramming();
      void cancelShortcutCapture().catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    if (!recording) return;
    let stop = false;
    let saved = false;
    const tick = async () => {
      const state = useHidraw.current ? await shortcutCaptureState() : captureSnapshot();
      if (stop || saved) return;
      const elapsed = Date.now() - startedAt.current;
      const idleTooLong = elapsed >= 8000 && state.phase !== "done" && (state.buttons || []).length === 0;
      if (state.phase === "done") {
        saved = true;
        setRecording(false);
        await save(state.buttons || []);
        return;
      }
      if (state.phase === "timeout" || state.phase === "cancelled" || idleTooLong) {
        saved = true;
        setRecording(false);
        cancelProgramming();
        void cancelShortcutCapture().catch(() => undefined);
        setMessage(state.phase === "timeout" || idleTooLong ? "Nothing pressed. Cancelled." : "Cancelled.");
        return;
      }
      setMessage(captureMessage(state.phase, state.buttons || [], state.seconds_left));
    };
    const id = window.setInterval(() => void tick(), 200);
    void tick();
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [recording]);

  async function save(next: string[]) {
    const result = await setPlayPauseButtons(next);
    if (!result.success) {
      setMessage(result.message || "Couldn't save that button.");
      return;
    }
    const saved = result.play_pause_buttons ?? next;
    setButtons(saved);
    setShortcutButtons(saved);
    setMessage(saved.length ? `Play/pause: ${bindingLabel(saved)}` : "Play/pause: Not set");
  }

  return (
    <PanelSection title="Play/pause button">
      <PanelSectionRow>
        <div style={{ whiteSpace: "normal" }}>
          {buttons.length ? `Play/pause: ${bindingLabel(buttons)}` : "Play/pause: Not set"}
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ whiteSpace: "normal", fontSize: "0.85em" }}>
          Default is R4. Tap Program, then press the button or combo you want.
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() => {
            if (recording) {
              cancelProgramming();
              void cancelShortcutCapture().catch(() => undefined);
              setRecording(false);
              setMessage("Cancelled.");
              return;
            }
            void (async () => {
              const status = await hidrawStatus().catch(() => ({ active: false }));
              useHidraw.current = Boolean(status.active);
              startedAt.current = Date.now();
              setRecording(true);
              setMessage("Release every button.");
              if (useHidraw.current) {
                const started = await startShortcutCapture();
                if (!started.success) {
                  useHidraw.current = false;
                  startProgramming(() => undefined);
                }
              } else {
                startProgramming(() => undefined);
              }
            })();
          }}
        >
          {recording ? "Cancel" : "Program button"}
        </ButtonItem>
      </PanelSectionRow>
      {buttons.length > 0 && !recording && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => {
              cancelProgramming();
              void cancelShortcutCapture().catch(() => undefined);
              setRecording(false);
              void save([]);
            }}
          >
            Clear
          </ButtonItem>
        </PanelSectionRow>
      )}
      {message && (
        <PanelSectionRow>
          <div style={{ whiteSpace: "normal" }}>{message}</div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

function SettingsView({
  ready,
  onBack,
  onSaved,
}: {
  ready: boolean;
  onBack: () => void;
  onSaved: () => void;
}) {
  const [serverUrl, setServerUrl] = useState("");
  const [serverName, setServerName] = useState("");
  const [token, setToken] = useState("");
  const [hasToken, setHasToken] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [username, setUsername] = useState("");
  const [authMethod, setAuthMethod] = useState("");
  const [libraries, setLibraries] = useState<LibraryInfo[]>([]);
  const [libraryKey, setLibraryKey] = useState("");
  const [servers, setServers] = useState<PlexServerChoice[]>([]);
  const [code, setCode] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [logLevel, setLogLevelName] = useState("info");

  async function loadLibraries(url: string, suppliedToken = "") {
    const result = await testConnection(url, suppliedToken);
    if (!result.success) {
      setLibraries([]);
      setError(result.message || "Couldn't reach the Plex server.");
      return;
    }
    setLibraries(result.libraries || []);
    setMessage(result.message || "");
    if (result.libraries.length === 1) setLibraryKey(result.libraries[0].key);
  }

  useEffect(() => {
    if (!ready) return;
    let stop = false;
    void (async () => {
      try {
        const settings = await getSettings();
        if (stop) return;
        setServerUrl(settings.server_url);
        setServerName(settings.server_name);
        setHasToken(settings.has_token);
        setSignedIn(settings.signed_in);
        setUsername(settings.username);
        setAuthMethod(settings.auth_method);
        setLibraryKey(settings.library_key);
        const levelName = settings.log_level || "info";
        setClientLogLevel(levelName);
        setLogLevelName(["error", "warning", "info", "debug"].includes(levelName) ? levelName : "info");
        if (settings.has_token && settings.server_url) {
          await loadLibraries(settings.server_url);
        } else if (settings.auth_method === "link" && settings.signed_in) {
          const listed = await listServers();
          if (!stop && listed.success) setServers(listed.servers || []);
          else if (!stop && listed.message) setError(listed.message);
        }
      } catch {
        if (!stop) setError("The plugin backend is not responding.");
      }
    })();
    return () => {
      stop = true;
    };
  }, [ready]);

  useEffect(() => {
    if (!code) return;
    let stop = false;
    const poll = async () => {
      try {
        const result = await pollSignIn();
        if (stop) return;
        if (result.expired) {
          setCode("");
          setError(result.message || "That code expired. Request a new one.");
          return;
        }
        if (!result.success && result.message) setError(result.message);
        if (result.signed_in) {
          setCode("");
          setSignedIn(true);
          setAuthMethod("link");
          setUsername(result.username || "");
          setServers(result.servers || []);
          setMessage(result.message || "Signed in. Choose a server.");
          setError(result.servers && result.servers.length > 0 ? "" : (result.message || ""));
        }
      } catch {
        if (!stop) setError("Can't reach plex.tv to sign in.");
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [code]);

  const selected = libraries.find((library) => library.key === libraryKey);

  async function onStartSignIn() {
    setBusy(true);
    setError("");
    setMessage("");
    setServers([]);
    try {
      const result = await startSignIn();
      if (!result.success || !result.code) {
        setError(result.message || "Couldn't start sign-in.");
        return;
      }
      setCode(result.code);
    } catch {
      setError("Can't reach plex.tv to sign in.");
    } finally {
      setBusy(false);
    }
  }

  async function onSelectServer(server: PlexServerChoice) {
    setBusy(true);
    setError("");
    try {
      const result = await selectServer(server.client_identifier);
      if (!result.success) {
        setError(result.message || "Couldn't use that server.");
        return;
      }
      const url = result.server_url || server.uri;
      setServerUrl(url);
      setServerName(result.server_name || server.name);
      setAuthMethod("link");
      setHasToken(true);
      setServers([]);
      setLibraryKey("");
      setMessage(server.local ? `Using the local server at ${url}.` : `Using ${url}.`);
      await loadLibraries(url);
    } catch {
      setError("Couldn't reach that Plex server.");
    } finally {
      setBusy(false);
    }
  }

  async function onChangeServer() {
    setBusy(true);
    setError("");
    try {
      const listed = await listServers();
      if (!listed.success) {
        setError(listed.message || "Couldn't load your Plex servers.");
        return;
      }
      setLibraries([]);
      setServers(listed.servers || []);
    } catch {
      setError("Can't reach plex.tv to list servers.");
    } finally {
      setBusy(false);
    }
  }

  async function onSignOut() {
    setBusy(true);
    setError("");
    try {
      const result = await signOut();
      if (!result.success) {
        setError(result.message || "Couldn't sign out.");
        return;
      }
      setSignedIn(false);
      setHasToken(false);
      setUsername("");
      setAuthMethod("");
      setServerUrl("");
      setServerName("");
      setLibraries([]);
      setLibraryKey("");
      setServers([]);
      setCode("");
      setToken("");
      setMessage("Signed out.");
    } catch {
      setError("Couldn't sign out.");
    } finally {
      setBusy(false);
    }
  }

  async function onTest() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await loadLibraries(serverUrl, token);
    } catch {
      setError("Can't reach your Plex server.");
    } finally {
      setBusy(false);
    }
  }

  async function onSave() {
    if (!libraryKey) {
      setError("Choose a library. Audiobooks are a Music library in Plex.");
      return;
    }
    const library = libraries.find((item) => item.key === libraryKey);
    setBusy(true);
    setError("");
    try {
      const result = await saveSettings(
        serverUrl,
        token,
        libraryKey,
        library?.title || "",
        library ? library.track_progress : null,
      );
      if (!result.success) {
        setError(result.message || "Couldn't save settings.");
        return;
      }
      setToken("");
      setHasToken(true);
      onSaved();
    } catch {
      setError("Couldn't save settings.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PanelSection title="Account">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onBack}>
            Back
          </ButtonItem>
        </PanelSectionRow>
        {signedIn && username && (
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal" }}>{`Signed in as ${username}`}</div>
          </PanelSectionRow>
        )}
        {hasToken && serverUrl && servers.length === 0 && !code && (
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal" }}>
              {serverName ? serverName : "Server"}
              {` · ${serverUrl}`}
            </div>
          </PanelSectionRow>
        )}
        {!code && servers.length === 0 && authMethod !== "link" && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void onStartSignIn()}>
              {busy ? "Working…" : signedIn ? "Sign in with Plex instead" : "Sign in with Plex"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {code && (
          <>
            <PanelSectionRow>
              <div style={{ width: "100%", textAlign: "center" }}>
                <div style={{ fontSize: "1.6em", letterSpacing: "0.25em", fontWeight: 700 }}>{code}</div>
                <div style={{ whiteSpace: "normal", marginTop: 6 }}>
                  On a phone or computer, open plex.tv/link and enter this code.
                </div>
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <div style={{ whiteSpace: "normal", opacity: 0.8 }}>Waiting for Plex…</div>
            </PanelSectionRow>
          </>
        )}
        {servers.map((server) => (
          <PanelSectionRow key={server.client_identifier || server.uri}>
            <ButtonItem layout="below" disabled={busy} onClick={() => void onSelectServer(server)}>
              <div style={{ whiteSpace: "normal", textAlign: "left" }}>
                <div>{server.name}</div>
                <div style={{ opacity: 0.75, fontSize: "0.85em" }}>
                  {server.uri}
                  {server.local ? " · local" : ""}
                </div>
              </div>
            </ButtonItem>
          </PanelSectionRow>
        ))}
        {authMethod === "link" && signedIn && !code && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void onChangeServer()}>
              Choose a different server
            </ButtonItem>
          </PanelSectionRow>
        )}
        {signedIn && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void onSignOut()}>
              Sign out
            </ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setAdvanced((open) => !open)}>
            {advanced ? "Hide advanced settings" : "Advanced: URL and token"}
          </ButtonItem>
        </PanelSectionRow>
        {message && (
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal" }}>{message}</div>
          </PanelSectionRow>
        )}
        <ErrorText text={error} />
      </PanelSection>
      {advanced && (
        <PanelSection title="Advanced">
          <PanelSectionRow>
            <TextField
              label="Plex URL"
              description="Example: http://192.168.1.50:32400"
              value={serverUrl}
              onChange={(event) => setServerUrl(event.target.value)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <TextField
              label="Plex token"
              description={hasToken ? "A token is saved on this Deck. Leave this blank to keep it." : "Stored only on this Deck. It is not logged."}
              value={token}
              bIsPassword={true}
              onChange={(event) => setToken(event.target.value)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void onTest()}>
              {busy ? "Working…" : "Test connection"}
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
      {libraries.length > 0 && (
        <PanelSection title="Choose a library">
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal" }}>
              Pick the library that holds your audiobooks.
            </div>
          </PanelSectionRow>
          {libraries.map((library) => {
            const chosen = library.key === libraryKey;
            return (
              <PanelSectionRow key={library.key}>
                <ButtonItem layout="below" onClick={() => setLibraryKey(library.key)}>
                  <div style={{ whiteSpace: "normal", textAlign: "left" }}>
                    <div style={{ fontWeight: chosen ? 700 : 400 }}>{library.title}</div>
                    <div style={{ opacity: 0.75, fontSize: "0.85em" }}>
                      {chosen ? "Selected" : "Tap to select"}
                    </div>
                  </div>
                </ButtonItem>
              </PanelSectionRow>
            );
          })}
          {selected && (
            <PanelSectionRow>
              <div style={{ whiteSpace: "normal", fontSize: "0.85em" }}>
                {selected.track_progress === false && (
                  <>
                    Resume in Plex: Off. Resume is controlled by Plex’s server setting “Store track progress”
                    (Plex web: Manage Library, Edit, Advanced). This plugin cannot change it. With it off, Plex only
                    marks whole tracks played and will not keep your spot for other Plex apps.
                  </>
                )}
                {selected.track_progress === true && (
                  <>
                    Resume in Plex: On. Plex’s “Store track progress” is on for this library, so your place in a file
                    can be saved for other Plex apps.
                  </>
                )}
                {selected.track_progress == null && (
                  <>
                    Resume in Plex: Unknown. This plugin could not read whether “Store track progress” is on. That
                    Plex setting (Manage Library, Edit, Advanced) controls whether other apps can resume mid-track.
                  </>
                )}
              </div>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy || !libraryKey} onClick={() => void onSave()}>
              Use this library
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
      <PanelSection title="Log">
        <PanelSectionRow>
          <DropdownItem
            label="Log level"
            description="Use Debug only when testing."
            rgOptions={[
              { data: "error", label: "Error" },
              { data: "warning", label: "Warning" },
              { data: "info", label: "Info" },
              { data: "debug", label: "Debug" },
            ]}
            selectedOption={logLevel}
            onChange={(option) => {
              const name = String(option?.data || "info");
              setLogLevelName(name);
              void (async () => {
                await setLogLevel(name);
                setClientLogLevel(name);
                if (name === "debug") replayRegistrationLog();
              })();
            }}
          />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
