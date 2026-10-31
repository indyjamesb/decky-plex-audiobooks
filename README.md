# Plex Audiobooks

A Decky Loader plugin for Steam Deck (SteamOS 3.9.x, Game Mode). It plays audiobooks from your own Plex Media Server while a game is running, and saves the listening position to Plex so any Plex client can resume it. Tested on Steam Deck OLED.

Version 0.1 is a small player. One server, one library, browse and search, background playback, and resume sync.

## Changelog

### 0.5.2

Tested on Steam Deck OLED. After a reboot the plugin waits for PipeWire before starting ffplay. If audio is still down, the panel says so instead of blaming the Plex server.

### 0.5.1

Tested on Steam Deck OLED. Game volume 100 sets every other stream to 100%, including a 12-channel game stream left at the ducked level. A plugin restart stops an ffplay the previous instance left paused.

### 0.5.0

First public release. Tested on Steam Deck OLED. Log level is a menu instead of a slider. If ffplay is missing, the panel says to install ffmpeg.

### 0.1.6

Tested on Steam Deck OLED. A Game volume slider sets other application streams, and the book can lower them while it plays. Pausing the book puts the game back. The book slider is labeled Book volume.

### 0.1.5

Tested on Steam Deck OLED. Play/pause still defaults to R4, and that name now means the Deck R4 or the Steam Controller right back button. Hidraw also reads an Xbox Wireless Controller over Bluetooth, a DualSense over Bluetooth, and a Switch Pro Controller over USB. A book page shows Resume and Chapters. The plugin keeps every Plex connection for the chosen server and fails over from local to remote to relay without asking. Requests to plex.tv and the media server identify this player from DMI (product "Plex Audiobooks for <device>", version, a client id saved once) and report `GET /:/timeline` about every 10 seconds and on play, pause, seek, and stop. That session display has not been checked against a real Plex server yet.

The store listing stays "for Steam Deck". On the device, `X-Plex-Device` and `X-Plex-Model` follow DMI. Valve Galileo maps to "Steam Deck OLED"; that mapping is device-verified. Valve Jupiter ("Steam Deck" LCD), Legion Go, Legion Go S, ROG Ally, ROG Ally X, and MSI Claw are published DMI names and were not checked on those devices. If DMI cannot be read, the name is "Linux Handheld".

Device-verified on a Steam Deck OLED, Oct 31 2026: Deck L4/R4, Steam Controller back buttons (28DE:1304 report 0x42), Xbox Wireless Controller Bluetooth (045E:0B13), DualSense Bluetooth (054C:0CE6 report 0x31), Switch Pro Controller USB (057E:2009 report 0x30).

Not captured, so untested: DualSense USB (report 0x01, buttons one byte earlier), Switch Pro Controller over Bluetooth (same 0x30 report), the generic evdev gamepad path, and any Steam Controller button other than the two back buttons. The LCD Deck (Jupiter) DMI mapping and the other handheld names are untested. Dashboard Now Playing for this player is also untested until it is tried on a real server.

### 0.1.4

Play/pause works while a game is focused. The Steam UI callback only runs when the Steam UI has focus, so the backend reads the Deck controller on `/dev/hidraw` and toggles playback itself. The default shortcut is R4. A saved action code 45 from 0.1.3 becomes R4. Program button waits until every button is released, then records the next press, so the A press that opens Program is not saved.

### 0.1.3

The shortcut used `RegisterForControllerInputMessages` after `RegisterForControllerStateChanges` disappeared. That path still only runs while the Steam UI is focused.

## Install on the Deck

Build the zip on a computer (or on the Deck, if Node is installed), then copy it over. Decky does not need its plugin store for this.

1. Install [Decky Loader](https://decky.xyz/) if it is not already installed.
2. Copy `release/plex-audiobooks-v0.5.2.zip` to the Deck. The zip in this repository is already built.
3. In Desktop Mode, or over SSH:

```bash
mkdir -p ~/homebrew/plugins
cd ~/homebrew/plugins
unzip -o /path/to/plex-audiobooks-v0.5.2.zip
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

`pnpm test` runs the Python unit tests. `pnpm run package` writes `release/plex-audiobooks-v0.5.2.zip`.

`pnpm run build` only produces `dist/index.js`. A manual install without the zip is the plugin directory containing `dist/`, `main.py`, `deck_hid.py`, `game_audio.py`, `player.py`, `plex.py`, `plex_auth.py`, `progress.py`, `plugin.json`, `package.json`, `LICENSE`, `NOTICE`, and `README.md`.

## Setup

Audiobooks in Plex are a **Music** library. There is no separate audiobook library type. Artist is the author, album is the book, and each file is a chapter or part. A single `.m4b` is one file.

### Sign in

In the plugin, choose **Sign in with Plex**. It asks plex.tv for a short code (`POST /api/v2/pins` with `strong=false`). On a phone or computer, open [plex.tv/link](https://plex.tv/link) and enter that code. The plugin polls the pin until Plex sets an auth token, then lists your servers from plex.tv's resource list. Pick one. When a server has a local connection, that address is used (for example `http://10.0.0.91:32400`) instead of a relay. Then pick the Music library and save.

**Sign out** clears the saved account, server, and library from this Deck and stops playback.

The account token and the server token are written to Decky's settings directory for this plugin, normally `~/homebrew/settings/plex-audiobooks/settings.json`, mode `0600`. They are sent only as the `X-Plex-Token` header. They are not written into stream URLs, log lines, or on-screen errors, and the panel never receives them back. They are still secrets sitting in a file on the device. Do not publish that file.

The plugin identifies itself to Plex as **Plex Audiobooks**, with one `X-Plex-Client-Identifier` generated the first time it runs and kept in that settings file.

### Advanced: URL and token

**Advanced: URL and token** is the fallback when link sign-in is not usable. Enter a server URL such as `http://10.0.0.91:32400` (`10.0.0.91:32400` is accepted and treated as `http://`) and a Plex token. In the Plex web app, play any item, open the browser's network panel, and copy `X-Plex-Token`. Plex's own article is [Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). Leave the token field blank later to keep the one already saved.

Test the connection, choose the Music library that holds the books, and press **Use this library**. The list is a picker, not a switch. The library list is only Music sections.

## Resume and Store track progress

Whether Plex keeps your place in a file is a setting on the library in Plex Media Server, not a switch in this plugin. The plugin can read it and cannot change it.

In Plex web: open the library, **Manage Library**, **Edit**, **Advanced**, and turn on **Store track progress**.

With that setting on, a position from another Plex app showed up in Plex as the track’s `viewOffset`, and this plugin can resume there and write the position back. With it off, Plex only stored `viewCount` (the track marked played). It did not keep a spot in the middle of a file, so other Plex apps cannot resume mid-track. The panel shows **Resume in Plex: On** or **Off** for the library you select.

## Playback in other apps

With **Store track progress** on, this plugin writes your place in each chapter file to Plex. Plex and Plexamp do not remember which file of a multi-file book you were on. Some other audiobook apps do not read progress back from Plex at all. A spot saved on the Deck may not show up there, and you may need to set it in that app yourself.

## How to listen

- **Library** lists books (Plex albums) with the author. The default order is recently played (`lastViewedAt`, then `addedAt`, then title). You can also sort by title, author, or recently added. The choice is saved. Search matches album titles. Open a book to see the cover, author, and files.
- **Play / Resume** reads Plex's saved progress and starts there. A finished book starts over.
- Playback is an `ffplay` process started by the Python backend. Closing the Quick Access Menu does not stop it, and a focused game does not stop it, as long as the Deck stays awake.
- **Pause**, **stop**, **-30s**, **+30s**, **previous**, and **next** are in the panel. Previous restarts the current file once you are a few seconds in; from the start of a file it goes back one file. Next marks the current file played in Plex and starts the next one.
- **Speed** moves in steps of 0.05 from 0.50x to 3.00x, so 1.20x is a real step. The last speed is saved. Changing it restarts the file at the current position.
- **Volume** is the audiobook only. On SteamOS it is applied with `pactl` when that exists. Otherwise it is applied the next time a file starts.
- The book bar is your place in the whole book. The chapter bar is your place in the current file.
- **Play/pause button** defaults to R4, including while a game is focused. The backend reads the Deck controller at `/dev/hidraw` (USB `28DE:1205`) and toggles playback itself, so it still works after the Quick Access Menu closes. One button fires on press. A combo fires when those buttons are held together. Repeats inside 400 ms are ignored. **Clear** turns it off. **Program button** waits until every button is up, then records the next press (release, or about 2 seconds from the first press). The A press that opens Program is not saved. The panel shows the result, for example `R4` or `L4 + R4`, and **Cancel** aborts. Leave a back button unbound in that game’s Steam Input layout; a combo can still reach the game. Touch bits are ignored.
- L4 (byte 13 bit `0x02`) and R4 (byte 13 bit `0x04`) were captured in a game. Other names follow Linux `drivers/hid/hid-steam.c` (`steam_do_deck_input_event`) and SDL `SDL_hidapi_steamdeck.c` (`SteamDeckButtons`). Those other names were not pressed in that capture. Set **Log level** to Debug. Lines look like `hidraw nodes hidraw2`, `hidraw input hidraw2`, `hidraw buttons R4`, and `hidraw shortcut fire R4`. Each toggle also writes one Info line: `Shortcut toggled playback (hidraw)`.
- `SteamClient.Input.RegisterForControllerInputMessages` is still registered for the Steam UI. It does not toggle while the hidraw reader is open, so one press is not applied twice. On the Steam client from Oct 31 2026 that callback does not run in a game.
- **Log level** is a menu in settings (Error, Warning, Info, Debug), saved, default Info. Controller diagnostics are Debug only. For a shortcut test, set Log level to Debug. Lines are written to `~/homebrew/logs/plex-audiobooks/plugin.log` with a `frontend:` prefix. Use Debug only when testing.

### Progress sync

Plex stores `viewOffset` per file, in milliseconds. For a multi-file book the plugin treats that the way Music Assistant's Plex audiobook support does: the resume point is the furthest file that still has an offset, and files before it count at their full length. A run of files that Plex has marked played, with the offset cleared, is skipped so the next file starts at the beginning. One `.m4b` is just one file, so `viewOffset` is the place inside that file.

While a file is playing, the plugin tells Plex with `GET /:/timeline` about every 15 seconds, and again on pause, stop, and seek. `time` and `playbackTime` are the position in milliseconds. The timeline duration is reported longer than the real file until the file actually finishes, because Plex otherwise marks an item watched at about 90% and drops the resume point. When a file finishes, or you press Next, the plugin scrobbles that file (`/:/scrobble`) so the position is saved for any Plex client.

Choosing a chapter, pressing Next or Previous, or skipping backward also rewrites nearby Plex progress: earlier unfinished files are marked played, and later saved offsets are cleared, so the file you are on is the resume point. That can mark files played that you did not listen through. Pause and the 15-second updates only move the current file's offset.

## What was tested here

This environment has no Steam Deck and no Plex server. These checks were run:

- Progress math: multi-file and single-file resume, ±30s across files, previous-chapter behavior, speed filter strings, the inflated timeline duration, and the "immediate ffplay exit is a failure" rule.
- Plex HTTP client against a local stand-in server: library filtering, book and chapter parsing, `viewOffset`, timeline query shape (`playbackTime` is the offset, not a clock), scrobble key, cover fetch, 401, and an unreachable server. The token stayed in the header and out of URLs and error text.
- Plex account client against a local stand-in for plex.tv: PIN creation with `strong=false`, polling until `authToken` is set, account name, and resource discovery that picks `http://10.0.0.91:32400` over a plex.direct address and a relay. Tokens stayed out of the panel payload and out of request URLs.
- `ffplay` itself, using a generated tone and a dummy audio driver: the process stays up, pause holds the estimated position, and a mid-file seek at 2× starts past that seek point. PipeWire on a Deck was not involved.

`pnpm run build` produces the frontend bundle the zip is built from. The Quick Access Menu was not opened, because that UI only exists inside the Steam client.

## Not tested

Sign-in and playback were confirmed on a Steam Deck OLED outside this environment. The items below were not verified here.

- Game Mode: audio continuing under a focused game, mixing with game audio, and the Quick Access Menu opening and closing during playback.
- The Deck's PipeWire socket (`/run/user/<uid>/pulse/native`) and Decky's bundled `LD_LIBRARY_PATH`, which is why ffplay is launched with a cleaned environment. That failure mode is real on Decky (Museck issues around ffplay dying instantly) but was not reproduced here.
- A real plex.tv sign-in, or a real Plex server accepting timeline updates, scrobbles, or direct file streaming with the token only in a header. If a file will not play, Plex may be refusing the header or the codec may need a transcode. This version direct-plays the file part. It does not ask Plex to transcode.
- The saved position actually showing up in another Plex client. The request shape matches what current Plex clients send. Whether the server stores it still depends on **Store track progress**.
- Sleep. When the Deck sleeps, playback stops. Nothing in this plugin keeps the device awake.
- Bluetooth output, remote Plex URLs, and HTTPS servers with a private certificate. The client verifies HTTPS certificates.
- Embedded chapters inside one `.m4b`. Next and previous follow Plex tracks. If Plex shows the book as one file, there is one chapter.
- This environment did not open `/dev/hidraw`. On a Deck, in-game captures confirmed L4 and R4 and a right-stick touch bit. The reader, the Program-button timing, and every other button name were not re-checked here.
- Whether a back button left unbound in a game’s Steam Input layout still shows up in the hidraw report. The capture was of the physical buttons, not of a game’s layout.
- Bluetooth Xbox and PlayStation controllers. They are not read in 0.5.0. See the roadmap.
- Position accuracy during a long stream. The position is wall-clock time since ffplay started, times the speed, plus the seek point. If the stream buffers, the bar can run ahead of the audio. Seeking restarts ffplay, so there is a short gap.
- Live volume when `pactl` is missing.

## Roadmap

Bluetooth Xbox and PlayStation pads are not in 0.5.0. The in-game shortcut reads the Deck’s built-in controller (`28DE:1205` on hidraw) and does not open evdev devices. A later build could watch those pads with evdev. TODO.

Not in 0.1.1. A separate **Game volume** slider, so a dungeon’s music can drop while the book stays up, is feasible and is not safe enough to ship yet.

SteamOS 3.9 uses PipeWire with a PulseAudio compatibility layer. Per-application volume is a sink-input volume, not a second system volume. Two Decky plugins already do this with `pactl`:

- This plugin’s book slider calls `pactl set-sink-input-volume` on the sink input whose `application.process.id` is the `ffplay` process (`set_process_volume` in `player.py`). If `pactl` is missing, that slider only applies the next time a file starts.
- [SteamDeck-VolumeMixer](https://github.com/jacklu97/SteamDeck-VolumeMixer) lists every sink input from `pactl list sink-inputs` (by `application.name`) and sets each one with `pactl set-sink-input-volume`. It does not need root. It also does not know which stream is “the game,” and it does not put the old volume back.

Game Mode has no per-game mixer of its own. On the desktop, the sound panel’s Playback Streams list shows the game, often under the audio engine’s name (FMOD, Unity, and similar) rather than the game title. That is the same stream `pactl` would change. See the [Steam Deck discussion on muting one game](https://steamcommunity.com/app/1675200/discussions/0/6955341523712010713/).

A later build can add one slider that:

1. Finds the foreground game. Steam’s running app is not the process that owns the audio. Proton and gamescope put the stream on a child process, and one game can open several streams. Matching has to walk that process tree, the same approach as [hyprvol](https://gist.github.com/Dregu/aa748bebc7c3aeddd3ceb55c0f046c5d) (gamescope, Proton, and multiple streams per window).
2. Remembers each matched sink input’s current volume, then sets only those inputs. Never touch the book’s `ffplay` input, and do nothing if the match is empty or ambiguous.
3. Looks the inputs up again on each slider move. Sink-input ids change when a game recreates its stream.
4. Restores the remembered volumes when the slider returns to its original level, when that game exits, and in plugin unload. If the plugin dies first, the game stays quiet until it opens a new stream.

That last step is why this stays off 0.1.1. A wrong pid match would turn down the wrong app, and a crash would leave the game ducked. `wpctl set-volume` is the native PipeWire equivalent of the same sink-input change ([PipeWire’s Pulse migration notes](https://gitlab.freedesktop.org/pipewire/pipewire/-/wikis/Migrate-PulseAudio)); it does not make the match any safer. The book volume slider stays as it is.

## Layout

The project follows the [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template): TypeScript in `src/` (`@decky/ui`, `@decky/api`) and a Python backend (`main.py`) that Decky loads. `deck_hid.py` reads the Deck controller, `plex_auth.py` talks to plex.tv, `plex.py` talks to a media server, `progress.py` is the resume math, and `player.py` owns ffplay.

## License

MIT. See `LICENSE` and `NOTICE` for Museck, Music Assistant, Chronicle, and the Decky template.
