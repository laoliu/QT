import fs from "node:fs/promises";
import path from "node:path";

/**
 * Atomic file write — writes to a .tmp file first, then renames.
 * `fs.rename()` is atomic on POSIX, preventing data corruption
 * if the process crashes mid-write.
 */
export async function atomicWriteFile(filePath: string, data: string): Promise<void> {
  const tmpPath = filePath + ".tmp";
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(tmpPath, data);
  await fs.rename(tmpPath, filePath);
}

/**
 * Simple async mutex for serializing concurrent operations.
 *
 * Used to prevent race conditions when multiple async tasks
 * need exclusive access to a shared resource (e.g., file I/O).
 */
export class AsyncMutex {
  private queue: (() => void)[] = [];
  private locked = false;

  async acquire(): Promise<() => void> {
    if (!this.locked) {
      this.locked = true;
      return () => this.release();
    }
    return new Promise<() => void>((resolve) => {
      this.queue.push(() => resolve(() => this.release()));
    });
  }

  private release(): void {
    const next = this.queue.shift();
    if (next) {
      next();
    } else {
      this.locked = false;
    }
  }
}
