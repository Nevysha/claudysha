export const date = (iso?: string | null) => (iso ? iso.slice(0, 10) : "");
export const datetime = (iso?: string | null) => (iso ? iso.slice(0, 16).replace("T", " ") : "");

export function size(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) {
    bytes /= 1024;
    i++;
  }
  return `${bytes.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
