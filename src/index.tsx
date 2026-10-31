import { definePlugin, staticClasses } from "@decky/ui";
import { FaBookOpen } from "react-icons/fa";

import { App } from "./App";
import { getSettings } from "./api";
import {
  setShortcutButtons,
  startControllerShortcut,
  stopControllerShortcut,
} from "./controllerShortcut";
import { setClientLogLevel } from "./logClient";

export default definePlugin(() => {
  // Registered once for the life of the plugin, not while the Quick Access
  // Menu is open. Decky Recorder does the same with window.SteamClient.
  void getSettings()
    .then((settings) => {
      setClientLogLevel(settings.log_level || "info");
      setShortcutButtons(settings.play_pause_actions || []);
      startControllerShortcut();
    })
    .catch(() => {
      startControllerShortcut();
    });
  return {
    name: "Plex Audiobooks",
    titleView: <div className={staticClasses.Title}>Plex Audiobooks</div>,
    content: <App />,
    icon: <FaBookOpen />,
    onDismount() {
      stopControllerShortcut();
    },
  };
});
