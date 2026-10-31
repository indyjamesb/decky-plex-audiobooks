# Plex Audiobooks

A Decky Loader plugin for Steam Deck (SteamOS 3.9.x, Game Mode). It plays audiobooks from your own Plex Media Server while a game is running, and writes your place back to Plex so [Prologue](https://prologue.audio/) on your phone can pick it up.

Version 0.1 is a small player, not a full Prologue clone. One server, one library, browse and search, background playback, and resume sync.

## Install on the Deck

Build the zip on a computer (or on the Deck, if Node is installed), then copy it over. Decky does not need its plugin store for this.

1. Install [Decky Loader](https://decky.xyz/) if it is not already installed.
2. Copy `release/plex-audiobooks-v0.1.0.zip` to the Deck. The zip in this repository is already built.
3. In Desktop Mode, or over SSH:

```bash
mkdir -p ~/homebrew/plugins
cd ~/homebrew/plugins
unzip -o /path/to/plex-audiobooks-v0.1.0.zip
```

The zip already contains a `plex-audiobooks` folder. You should end up with:

```text
~/homebrew/plugins/plex-audiobooks/dist/index.js
~/homebrew/plugins/plex-audiobooks/main.py
~/homebrew/plugins/plex-audiobooks/plugin.json
```

4. Restart Decky:

```bash
sudo systemctl restart plugin_loader
```

You can also restart it from Decky's settings menu if that control is present. Then open the Quick Access Menu (the `...` button) and choose **Plex Audiobooks**.

`ffplay` comes with SteamOS's ffmpeg (`/usr/bin/ffplay`). If a playback error says ffplay is missing, install ffmpeg from Desktop Mode and try again.

## Build

Node.js 18 or newer, pnpm 9 or newer, and Python 3.11 or newer.

```bash
pnpm install
pnpm test
pnpm run package
```

`pnpm test` runs the Python unit tests. `pnpm run package` writes `release/plex-audiobooks-v0.1.0.zip`.

`pnpm run build` only produces `dist/index.js`. A manual install without the zip is the plugin directory containing `dist/`, `main.py`, `player.py`, `plex.py`, `progress.py`, `plugin.json`, `package.json`, `LICENSE`, `NOTICE`, and `README.md`.

## Setup

Audiobooks in Plex are a **Music** library. There is no separate audiobook library type. Tag them the way Prologue expects: artist is the author, album is the book, and each file is a chapter or part. A single `.m4b` is one file.

In Plex, edit that library, open **Advanced**, and turn on **Store track progress**. Without it, Plex does not keep a per-file resume point for music, and neither this plugin nor Prologue can sync a position in the middle of a file.

The plugin asks for two things, both typed on the Deck:

- **Server URL.** A local address such as `http://10.0.0.91:32400`. `10.0.0.91:32400` is accepted and treated as `http://`.
- **Plex token.** In the Plex web app, play any item, open the browser's network panel, and copy the `X-Plex-Token` value. Plex's own article is [Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).

The token is written to Decky's settings directory for this plugin, normally `~/homebrew/settings/plex-audiobooks/settings.json`, mode `0600`. It is sent to Plex only as the `X-Plex-Token` header. It is not written into stream URLs, log lines, or on-screen errors. The settings screen does not send the saved token back to the panel; leave the field blank to keep the saved one. It is still a secret sitting in a file on the device. Do not publish that file.

Test the connection, pick the Music library that holds the books, and save. The library list is only Music sections. If Plex reports that Store track progress is off, the panel says so.

## How to listen

- **Library** lists books (Plex albums) with the author. Search matches album titles. Open a book to see the cover, author, and files.
- **Play / Resume** reads Plex's saved progress and starts there. A finished book starts over.
- Playback is an `ffplay` process started by the Python backend. Closing the Quick Access Menu does not stop it, and a focused game does not stop it, as long as the Deck stays awake.
- **Pause**, **stop**, **-30s**, **+30s**, **previous**, and **next** are in the panel. Previous restarts the current file once you are a few seconds in; from the start of a file it goes back one file. Next marks the current file played in Plex and starts the next one.
- **Speed** cycles 0.75× through 3×. Changing speed restarts the file at the current position.
- **Volume** is the audiobook only. On SteamOS it is applied with `pactl` when that exists. Otherwise it is applied the next time a file starts.
- The book bar is your place in the whole book. The chapter bar is your place in the current file.

### Progress sync

Plex stores `viewOffset` per file, in milliseconds. For a multi-file book the plugin treats that the way Music Assistant's Plex audiobook support does: the resume point is the furthest file that still has an offset, and files before it count at their full length. A run of files that Plex has marked played, with the offset cleared, is skipped so the next file starts at the beginning. One `.m4b` is just one file, so `viewOffset` is the place inside that file.

While a file is playing, the plugin tells Plex with `GET /:/timeline` about every 15 seconds, and again on pause, stop, and seek. `time` and `playbackTime` are the position in milliseconds. The timeline duration is reported longer than the real file until the file actually finishes, because Plex otherwise marks an item watched at about 90% and drops the resume point. When a file finishes, or you press Next, the plugin scrobbles that file (`/:/scrobble`) so Prologue can move on.

Choosing a chapter, pressing Next or Previous, or skipping backward also rewrites nearby Plex progress: earlier unfinished files are marked played, and later saved offsets are cleared, so the file you are on is the resume point. That can mark files played that you did not listen through. Pause and the 15-second updates only move the current file's offset.

## What was tested here

This environment has no Steam Deck and no Plex server. These checks were run:

- Progress math: multi-file and single-file resume, ±30s across files, previous-chapter behavior, speed filter strings, the inflated timeline duration, and the "immediate ffplay exit is a failure" rule.
- Plex HTTP client against a local stand-in server: library filtering, book and chapter parsing, `viewOffset`, timeline query shape (`playbackTime` is the offset, not a clock), scrobble key, cover fetch, 401, and an unreachable server. The token stayed in the header and out of URLs and error text.
- `ffplay` itself, using a generated tone and a dummy audio driver: the process stays up, pause holds the estimated position, and a mid-file seek at 2× starts past that seek point. PipeWire on a Deck was not involved.

`pnpm run build` produces the frontend bundle the zip is built from. The Quick Access Menu was not opened, because that UI only exists inside the Steam client.

## Not tested

Nothing below has been tried on hardware or against a real Plex Media Server. Treat it as unverified until you do.

- Game Mode: audio continuing under a focused game, mixing with game audio, and the Quick Access Menu opening and closing during playback.
- The Deck's PipeWire socket (`/run/user/<uid>/pulse/native`) and Decky's bundled `LD_LIBRARY_PATH`, which is why ffplay is launched with a cleaned environment. That failure mode is real on Decky (Museck issues around ffplay dying instantly) but was not reproduced here.
- A real Plex server accepting timeline updates, scrobbles, and direct file streaming with the token only in a header. If a file will not play, Plex may be refusing the header or the codec may need a transcode. This version direct-plays the file part. It does not ask Plex to transcode.
- Resume actually appearing in Prologue, or Prologue's position appearing here. The request shape matches what current Plex clients and Music Assistant send. Whether your server stores it still depends on **Store track progress**.
- Sleep. When the Deck sleeps, playback stops. Nothing in this plugin keeps the device awake.
- Bluetooth output, remote Plex URLs, and HTTPS servers with a private certificate. The client verifies HTTPS certificates.
- Embedded chapters inside one `.m4b`. Next and previous follow Plex tracks. If Plex shows the book as one file, there is one chapter.
- Position accuracy during a long stream. The position is wall-clock time since ffplay started, times the speed, plus the seek point. If the stream buffers, the bar can run ahead of the audio. Seeking restarts ffplay, so there is a short gap.
- Live volume when `pactl` is missing.

## Layout

The project follows the [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template): TypeScript in `src/` (`@decky/ui`, `@decky/api`) and a Python backend (`main.py`) that Decky loads. `plex.py` talks to Plex, `progress.py` is the resume math, and `player.py` owns ffplay.

## License

MIT. See `LICENSE` and `NOTICE` for Museck, Music Assistant, Chronicle, and the Decky template.
