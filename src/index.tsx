import { definePlugin, staticClasses } from "@decky/ui";
import { FaBookOpen } from "react-icons/fa";

import { App } from "./App";
import { getSettings } from "./api";
import { setShortcutConfig, startControllerShortcut, stopControllerShortcut } from "./controllerShortcut";

export default definePlugin(() => {
  // The listener lives for the life of the plugin, not the Quick Access Menu,
  // so a button can toggle playback while the menu is closed.
  startControllerShortcut();
  void getSettings()
    .then((settings) => {
      const mode = settings.play_pause_mode;
      setShortcutConfig({
        mode: mode === "back" || mode === "combo" ? mode : "off",
        backButton: settings.play_pause_back === "L5" || settings.play_pause_back === "R4" || settings.play_pause_back === "R5"
          ? settings.play_pause_back
          : "L4",
        combo: settings.play_pause_combo || [],
      });
    })
    .catch(() => undefined);
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
