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

export const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];

export function nextSpeed(current: number): number {
  const index = SPEEDS.findIndex((speed) => Math.abs(speed - current) < 0.01);
  return SPEEDS[(index + 1) % SPEEDS.length];
}

export function formatSpeed(speed: number): string {
  return `${speed}×`;
}
