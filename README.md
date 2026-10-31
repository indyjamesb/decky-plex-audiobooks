# Plex Audiobooks

A Decky Loader plugin for Steam Deck (SteamOS 3.9.x, Game Mode). It plays audiobooks from your own Plex Media Server while a game is running, and saves the listening position to Plex so any Plex client can resume it. Tested on Steam Deck OLED.

This plugin was built with AI coding assistance, and directed and tested on a Steam Deck OLED by the maintainer.

Features: one Plex server and library, browse and search, playback in the background while a game runs, resume sync with Plex, an in-game play/pause shortcut (R4 by default; Steam Controller, Xbox, DualSense and Switch Pro buttons also work), a Game volume slider with optional auto-lowering while the book plays, and automatic switching between your local and remote Plex connection.

## Changelog

### 0.5.2

First public release. Tested on Steam Deck OLED. After a reboot the plugin waits for PipeWire before starting ffplay. If audio is still down, the panel says so instead of blaming the Plex server.

### 0.5.1

Tested on Steam Deck OLED. Game volume 100 sets every other stream to 100%, including a 12-channel game stream left at the ducked level. A plugin restart stops an ffplay the previous instance left paused.

### 0.5.0

Tested on Steam Deck OLED. Log level is a menu instead of a slider. If ffplay is missing, the panel says to install ffmpeg.

### 0.1.6

Tested on Steam Deck OLED. A Game volume slider sets other application streams, and the book can lower them while it plays. Pausing the book puts the game back. The book slider is labeled Book volume.

### 0.1.5

Tested on Steam Deck OLED. Play/pause still defaults to R4, and that name now means the Deck R4 or the Steam Controller right back button. Hidraw also reads an Xbox Wireless Controller over Bluetooth, a DualSense over Bluetooth, and a Switch Pro Controller over USB. A book page shows Resume and Chapters. The plugin keeps every Plex connection for the chosen server and fails over from local to remote to relay without asking. Requests to plex.tv and the media server identify this player from DMI (product "Plex Audiobooks for <device>", version, a client id saved once) and report `GET /:/timeline` about every 10 seconds and on play, pause, seek, and stop. Plex Dashboard shows it under Now Playing.

The store listing stays "for Steam Deck". On the device, `X-Plex-Device` and `X-Plex-Model` follow DMI. Valve Galileo maps to "Steam Deck OLED"; that mapping is device-verified. Valve Jupiter ("Steam Deck" LCD), Legion Go, Legion Go S, ROG Ally, ROG Ally X, and MSI Claw are published DMI names and were not checked on those devices. If DMI cannot be read, the name is "Linux Handheld".

Device-verified on a Steam Deck OLED, Oct 31 2026: Deck L4/R4, Steam Controller back buttons (28DE:1304 report 0x42), Xbox Wireless Controller Bluetooth (045E:0B13), DualSense Bluetooth (054C:0CE6 report 0x31), Switch Pro Controller USB (057E:2009 report 0x30).

Not captured, so untested: DualSense USB (report 0x01, buttons one byte earlier), Switch Pro Controller over Bluetooth (same 0x30 report), the generic evdev gamepad path, and any Steam Controller button other than the two back buttons. The LCD Deck (Jupiter) DMI mapping and the other handheld names are untested.

### 0.1.4

Play/pause works while a game is focused. The Steam UI callback only runs when the Steam UI has focus, so the backend reads the Deck controller on `/dev/hidraw` and toggles playback itself. The default shortcut is R4. A saved action code 45 from 0.1.3 becomes R4. Program button waits until every button is released, then records the next press, so the A press that opens Program is not saved.

### 0.1.3

The shortcut used `RegisterForControllerInputMessages` after `RegisterForControllerStateChanges` disappeared. That path still only runs while the Steam UI is focused.

## Install on the Deck

This plugin is not in the Decky plugin store. It is unofficial and is not affiliated with Plex.

You need [Decky Loader](https://decky.xyz/), a Plex server with a Music library of audiobooks, and `ffplay`. SteamOS ships `ffplay` with ffmpeg (`/usr/bin/ffplay`). If a playback error says ffplay is missing, install ffmpeg from Desktop Mode and try again.

Download `plex-audiobooks-v0.5.2.zip` from the [latest release](https://github.com/indyjamesb/decky-plex-audiobooks/releases/latest).

1. Open the Quick Access menu (the `...` button) and open Decky. The title bar says **Decky**. Press the gear (its description is **Open Decky Settings**).
2. Open **General**. Under **Other**, turn on **Developer mode**. A **Developer** page then appears in the settings sidebar.
3. Open **Developer**. Under **Third-Party Plugins**, find **Install Plugin from ZIP File** and press **Browse**.
4. The first time, Decky shows **Third-Party Plugin Installation** and makes you wait 5 seconds before **Continue**. That dialog says the Decky Loader team has not reviewed the plugin. **Browse** then opens a zip file picker in your home directory.

Decky extracts the zip into `~/homebrew/plugins`. The archive has one top-level folder, `plex-audiobooks/`, and `plugin.json` sits directly inside it. You should end up with:

```text
~/homebrew/plugins/plex-audiobooks/plugin.json
~/homebrew/plugins/plex-audiobooks/main.py
~/homebrew/plugins/plex-audiobooks/dist/index.js
```

Open the Quick Access menu and choose **Plex Audiobooks**.

### Update

Install the newer zip the same way. Decky extracts it over `~/homebrew/plugins/plex-audiobooks` and loads the plugin again. Saved settings stay in `~/homebrew/settings/plex-audiobooks/`.

### Uninstall

In Decky settings, open **Plugins**. On the Plex Audiobooks row, open the `...` menu (**Plugin Actions**) and choose **Uninstall**. Confirm **Uninstall Plex Audiobooks**. That removes the plugin folder. The settings file, which holds the Plex token, stays at `~/homebrew/settings/plex-audiobooks/settings.json`. Delete that file if you want the token gone.

### Manual install

Over SSH, you can unpack the zip yourself:

```bash
sudo mkdir -p ~/homebrew/plugins
sudo unzip -o /path/to/plex-audiobooks-v0.5.2.zip -d ~/homebrew/plugins
sudo systemctl restart plugin_loader
```

The zip already contains a `plex-audiobooks` folder, so the files land in `~/homebrew/plugins/plex-audiobooks/`.

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

In the plugin, choose **Sign in with Plex**. It asks plex.tv for a short code (`POST /api/v2/pins` with `strong=false`). On a phone or computer, open [plex.tv/link](https://plex.tv/link) and enter that code. The plugin polls the pin until Plex sets an auth token, then lists your servers from plex.tv's resource list. Pick one. When a server has a local connection, that address is used (for example `http://192.168.1.50:32400`) instead of a relay. Then pick the Music library and save.

**Sign out** clears the saved account, server, and library from this Deck and stops playback.

The account token and the server token are written to Decky's settings directory for this plugin, normally `~/homebrew/settings/plex-audiobooks/settings.json`, mode `0600`. They are sent only as the `X-Plex-Token` header. They are not written into stream URLs, log lines, or on-screen errors, and the panel never receives them back. They are still secrets sitting in a file on the device. Do not publish that file.

The plugin identifies itself to Plex as **Plex Audiobooks**, with one `X-Plex-Client-Identifier` generated the first time it runs and kept in that settings file.

### Advanced: URL and token

**Advanced: URL and token** is the fallback when link sign-in is not usable. Enter a server URL such as `http://192.168.1.50:32400` (`192.168.1.50:32400` is accepted and treated as `http://`) and a Plex token. In the Plex web app, play any item, open the browser's network panel, and copy `X-Plex-Token`. Plex's own article is [Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). Leave the token field blank later to keep the one already saved.

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
- **Book volume** is the audiobook only. On SteamOS it is applied with `pactl` when that exists. Otherwise it is applied the next time a file starts.
- **Game volume** sets every other app's audio, such as the game, without touching the book. 100 is full volume.
- **Lower game while book plays** turns the game down to the **Lowered game volume** level while the book plays, and puts it back when you pause or stop. Book and game audio go to whatever output the Deck is using: speakers, Bluetooth headphones, or the dock.
- The book bar is your place in the whole book. The chapter bar is your place in the current file.
- **Play/pause button** defaults to R4, including while a game is focused. The backend reads the controller at `/dev/hidraw` (the Deck's built-in controller, a Steam Controller, an Xbox Wireless Controller or DualSense over Bluetooth, or a Switch Pro Controller over USB) and toggles playback itself, so it still works after the Quick Access Menu closes. One button fires on press. A combo fires when those buttons are held together. Repeats inside 400 ms are ignored. **Clear** turns it off. **Program button** waits until every button is up, then records the next press (release, or about 2 seconds from the first press). The A press that opens Program is not saved. The panel shows the result, for example `R4` or `L4 + R4`, and **Cancel** aborts. Leave a back button unbound in that game’s Steam Input layout; a combo can still reach the game. Touch bits are ignored.
- L4 (byte 13 bit `0x02`) and R4 (byte 13 bit `0x04`) were captured in a game. Other Deck button names follow Linux `drivers/hid/hid-steam.c` (`steam_do_deck_input_event`) and SDL `SDL_hidapi_steamdeck.c` (`SteamDeckButtons`). Set **Log level** to Debug. Lines look like `hidraw nodes hidraw2`, `hidraw input hidraw2`, `hidraw buttons R4`, and `hidraw shortcut fire R4`. Each toggle also writes one Info line: `Shortcut toggled playback (hidraw)`.
- `SteamClient.Input.RegisterForControllerInputMessages` is still registered for the Steam UI. It does not toggle while the hidraw reader is open, so one press is not applied twice. On the Steam client from Oct 31 2026 that callback does not run in a game.
- **Log level** is a menu in settings. See [Logs](#logs). Controller diagnostics are Debug only. For a shortcut test, set Log level to Debug.

### Progress sync

Plex stores `viewOffset` per file, in milliseconds. For a multi-file book the plugin treats that the way Music Assistant's Plex audiobook support does: the resume point is the furthest file that still has an offset, and files before it count at their full length. A run of files that Plex has marked played, with the offset cleared, is skipped so the next file starts at the beginning. One `.m4b` is just one file, so `viewOffset` is the place inside that file.

While a file is playing, the plugin tells Plex with `GET /:/timeline` about every 10 seconds, and again on pause, stop, and seek. `time` and `playbackTime` are the position in milliseconds. The timeline duration is reported longer than the real file until the file actually finishes, because Plex otherwise marks an item watched at about 90% and drops the resume point. When a file finishes, or you press Next, the plugin scrobbles that file (`/:/scrobble`) so the position is saved for any Plex client.

Choosing a chapter, pressing Next or Previous, or skipping backward also rewrites nearby Plex progress: earlier unfinished files are marked played, and later saved offsets are cleared, so the file you are on is the resume point. That can mark files played that you did not listen through. Pause and the 10-second updates only move the current file's offset.

## Logs

**Log level** is a menu in the plugin settings: Error, Warning, Info, or Debug. The choice is saved. The default is Info.

Each time the plugin loads, Decky starts a new file named with the time, `~/homebrew/logs/plex-audiobooks/YYYY-MM-DD HH.MM.SS.log`. It keeps the 5 newest `.log` files and deletes the older ones. A single file is not size-capped, so leave Debug on only while you are testing. Lines from the panel are prefixed `frontend:`.

- **Error** records real failures: ffplay missing or crashing, audio not ready, Plex unreachable, a book failing to load or list, sign-in failing, and settings failing to read or save.
- **Warning** also records problems that do not stop playback: a progress report or scrobble that failed, a game-volume change or restore that failed, and `pactl` failures.
- **Info** also records ordinary activity: load and unload, what is playing, shortcut presses, server switches, sign-in and sign-out, and setting changes.
- **Debug** also records connection attempts, local checks, and controller diagnostics.

## Tested

On a Steam Deck OLED (SteamOS 3.9.5) with a real Plex Media Server:

- Plex sign-in, server and library picking, browsing, search, and playback.
- Audio continuing under a focused game, mixed with game audio, with the Quick Access Menu opening and closing.
- Output to the Deck speakers and to Bluetooth headphones (AirPods Max).
- Resume from Plex, progress written back to Plex, and the player showing in Plex Dashboard under Now Playing.
- Falling back from the local server address to the remote one away from home.
- Game volume, lowering the game while the book plays, and restoring it on pause.
- The play/pause shortcut in a game with the Deck L4/R4, the Steam Controller back buttons, an Xbox Wireless Controller and DualSense over Bluetooth, and a Switch Pro Controller over USB.
- Playback right after a reboot, before the Deck's audio has finished starting.

The 97 unit tests cover the progress math, the Plex and plex.tv clients against local stand-in servers (tokens stay in headers and out of URLs and errors), the controller report parsing, game-volume handling, and ffplay start, pause, and seek.

## Not tested

- Other handhelds and the LCD Steam Deck. The device names for them come from published DMI values.
- DualSense over USB, a Switch Pro Controller over Bluetooth, and Steam Controller buttons other than the two back buttons.
- Whether a button bound in a game's Steam Input layout still reaches the shortcut. Leave the shortcut button unbound in that game to be safe.
- HTTPS servers with a private certificate. The plugin verifies HTTPS certificates, so a self-signed one is expected to fail.
- Embedded chapters inside one `.m4b`. Next and previous follow Plex tracks, so a book Plex shows as one file is one chapter.
- Volume changes while playing when `pactl` is missing.

## Known limits

- Sleep stops playback. Nothing in the plugin keeps the Deck awake.
- Files are direct-played. The plugin never asks Plex to transcode, so a codec ffplay can't read won't play.
- The position bar is estimated from elapsed time and speed. If the stream buffers, the bar can run slightly ahead of the audio. Seeking restarts ffplay, so there's a short gap.

## Layout

The project follows the [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template): TypeScript in `src/` (`@decky/ui`, `@decky/api`) and a Python backend (`main.py`) that Decky loads. `deck_hid.py` reads the Deck controller, `plex_auth.py` talks to plex.tv, `plex.py` talks to a media server, `progress.py` is the resume math, and `player.py` owns ffplay.

## License

MIT. See `LICENSE` and `NOTICE` for Museck, Music Assistant, Chronicle, and the Decky template.
