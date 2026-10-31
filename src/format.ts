export function formatTime(ms: number): string {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** Nearest 0.05 step from 0.50 through 3.00. 1.2 stays 1.2. */
export function snapSpeed(value: number): number {
  if (!Number.isFinite(value)) return 1;
  const steps = Math.round((value - 0.5) / 0.05);
  const clamped = Math.max(0, Math.min(50, steps));
  return Math.round((0.5 + clamped * 0.05) * 100) / 100;
}

export function stepSpeed(value: number, direction: -1 | 1): number {
  return snapSpeed(snapSpeed(value) + direction * 0.05);
}

export function formatSpeed(speed: number): string {
  return `${snapSpeed(speed).toFixed(2)}x`;
}

export const BOOK_SORTS = [
  { id: "recently_played", label: "Recently played" },
  { id: "title", label: "Title A–Z" },
  { id: "author", label: "Author" },
  { id: "recently_added", label: "Recently added" },
] as const;

export type BookSortId = (typeof BOOK_SORTS)[number]["id"];
