import {
  ButtonItem,
  DialogButton,
  Focusable,
  PanelSection,
  PanelSectionRow,
  SliderField,
  TextField,
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
  nextChapter,
  playBook,
  pollSignIn,
  previousChapter,
  saveSettings,
  selectServer,
  setSpeed,
  setVolume,
  signOut,
  skip,
  startSignIn,
  stopPlayback,
  testConnection,
  togglePlayback,
} from "./api";
import { formatSpeed, formatTime, nextSpeed } from "./format";

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
  const volumeTimer = useRef<number | null>(null);
  const shownVolume = volume !== null ? volume : status?.volume ?? 80;

  useEffect(() => {
    return () => {
      if (volumeTimer.current !== null) window.clearTimeout(volumeTimer.current);
    };
  }, []);

  const book = status?.has_book ? status : null;
  const playing = Boolean(status?.playing);
  const speed = status?.speed || 1;

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
          <ButtonItem layout="below" onClick={() => void onRun(() => setSpeed(nextSpeed(speed)))} disabled={!book}>
            {`Speed ${formatSpeed(speed)}`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void onRun(() => stopPlayback())} disabled={!book}>
            Stop and save progress
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Volume"
            description="Applied to the audiobook, not the game."
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
        {status?.track_progress === false && (
          <PanelSectionRow>
            <div style={{ whiteSpace: "normal", fontSize: "0.85em" }}>
              This library does not have Store track progress turned on. Plex may not keep a resume point. Edit the
              library in Plex, open Advanced, and enable Store track progress.
            </div>
          </PanelSectionRow>
        )}
        <ErrorText text={error} />
      </PanelSection>
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
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<BookDetail | null>(null);

  async function load(search: string, start: number, append: boolean) {
    setLoading(true);
    setError("");
    try {
      const result = await listBooks(search, start);
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
    void load("", 0, false);
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
  const book = detail.book;
  if (!book) return null;
  const resumeLabel = detail.finished
    ? "Play from the start"
    : detail.resume_offset_ms > 0 || detail.resume_index > 0
      ? `Resume at ${formatTime(detail.book_position_ms)}`
      : "Play";

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
      setError("Can't reach the Plex server.");
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
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={starting} onClick={() => void start(-1, 0)}>
          {starting ? "Starting…" : resumeLabel}
        </ButtonItem>
      </PanelSectionRow>
      <ErrorText text={error} />
      {detail.tracks.map((chapter) => (
        <PanelSectionRow key={chapter.index}>
          <ButtonItem
            layout="below"
            disabled={starting}
            onClick={() => void start(chapter.index, 0)}
          >
            <div style={{ whiteSpace: "normal", textAlign: "left" }}>
              {chapter.title || `Chapter ${chapter.index + 1}`}
              <span style={{ opacity: 0.7 }}>{` · ${formatTime(chapter.duration_ms)}`}</span>
            </div>
          </ButtonItem>
        </PanelSectionRow>
      ))}
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
      setError("Can't reach the Plex server.");
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
              description="Example: http://10.0.0.91:32400"
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
        <PanelSection title="Library">
          {libraries.map((library) => (
            <PanelSectionRow key={library.key}>
              <ButtonItem layout="below" onClick={() => setLibraryKey(library.key)}>
                <div style={{ whiteSpace: "normal", textAlign: "left" }}>
                  {library.key === libraryKey ? "● " : "○ "}
                  {library.title}
                  {library.track_progress === false ? " · track progress off" : ""}
                </div>
              </ButtonItem>
            </PanelSectionRow>
          ))}
          {selected?.track_progress === false && (
            <PanelSectionRow>
              <div style={{ whiteSpace: "normal", fontSize: "0.85em" }}>
                Turn on Store track progress for this library in Plex (Manage Library, Edit, Advanced). Without it,
                Plex does not keep a resume point that other clients can pick up.
              </div>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void onSave()}>
              Save library
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
    </>
  );
}
