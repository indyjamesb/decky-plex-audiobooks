import { definePlugin, staticClasses } from "@decky/ui";
import { FaBookOpen } from "react-icons/fa";

import { App } from "./App";

export default definePlugin(() => {
  return {
    name: "Plex Audiobooks",
    titleView: <div className={staticClasses.Title}>Plex Audiobooks</div>,
    content: <App />,
    icon: <FaBookOpen />,
    onDismount() {
      // Closing the Quick Access Menu unmounts the panel. The Python backend
      // keeps ffplay running until the plugin itself is unloaded.
    },
  };
});
