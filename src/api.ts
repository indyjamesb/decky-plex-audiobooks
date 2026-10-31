import { callable } from "@decky/api";

export interface LibraryInfo {
  key: string;
  title: string;
  track_progress: boolean | null;
}

export interface Settings {
  server_url: string;
  server_name: string;
  username: string;
  signed_in: boolean;
  auth_method: string;
  has_token: boolean;
  library_key: string;
  library_title: string;
  track_progress: boolean | null;
  speed: number;
  volume: number;
}

export interface PlexServerChoice {
  client_identifier: string;
  name: string;
  uri: string;
  local: boolean;
}

export interface SignInResult extends ActionResult {
  pending?: boolean;
  signed_in?: boolean;
  username?: string;
  code?: string;
  link?: string;
  servers?: PlexServerChoice[];
  expired?: boolean;
  server_url?: string;
  server_name?: string;
  local?: boolean;
}

export interface ActionResult {
  success: boolean;
  message?: string;
  speed?: number;
  volume?: number;
  applied?: boolean;
}

export interface ConnectionResult extends ActionResult {
  server_name?: string;
  libraries: LibraryInfo[];
}

export interface BookSummary {
  rating_key: string;
  title: string;
  author: string;
  duration_ms: number;
  leaf_count: number;
  viewed_leaf_count: number;
  year?: number | null;
  cover: string;
}

export interface BookListResult extends ActionResult {
  books: BookSummary[];
  total: number;
  start?: number;
}

export interface ChapterSummary {
  title: string;
  duration_ms: number;
  index: number;
}

export interface BookDetail extends ActionResult {
  book?: BookSummary;
  tracks: ChapterSummary[];
  resume_index: number;
  resume_offset_ms: number;
  finished: boolean;
  book_position_ms: number;
  book_duration_ms: number;
  cover: string;
}

export interface PlaybackStatus {
  has_book: boolean;
  playing: boolean;
  paused: boolean;
  title: string;
  author: string;
  cover: string;
  chapter_title: string;
  chapter_index: number;
  chapter_count: number;
  chapter_position_ms: number;
  chapter_duration_ms: number;
  book_position_ms: number;
  book_duration_ms: number;
  speed: number;
  volume: number;
  last_error: string | null;
  album_key: string;
  configured: boolean;
  library_title: string;
  track_progress: boolean | null;
}

export const getSettings = callable<[], Settings>("get_settings");
export const saveSettings = callable<
  [serverUrl: string, token: string, libraryKey: string, libraryTitle: string, trackProgress: boolean | null],
  ActionResult
>("save_settings");
export const testConnection = callable<[serverUrl: string, token: string], ConnectionResult>("test_connection");
export const listBooks = callable<[query: string, start: number], BookListResult>("list_books");
export const getBook = callable<[albumKey: string], BookDetail>("get_book");
export const playBook = callable<[albumKey: string, trackIndex: number, offsetMs: number], ActionResult>("play_book");
export const togglePlayback = callable<[], ActionResult>("toggle_playback");
export const stopPlayback = callable<[], ActionResult>("stop_playback");
export const skip = callable<[deltaMs: number], ActionResult>("skip");
export const nextChapter = callable<[], ActionResult>("next_chapter");
export const previousChapter = callable<[], ActionResult>("previous_chapter");
export const setSpeed = callable<[speed: number], ActionResult>("set_speed");
export const setVolume = callable<[volume: number], ActionResult>("set_volume");
export const getStatus = callable<[], PlaybackStatus>("get_status");
export const startSignIn = callable<[], SignInResult>("start_sign_in");
export const pollSignIn = callable<[], SignInResult>("poll_sign_in");
export const listServers = callable<[], SignInResult>("list_servers");
export const selectServer = callable<[clientIdentifier: string], SignInResult>("select_server");
export const signOut = callable<[], ActionResult>("sign_out");
