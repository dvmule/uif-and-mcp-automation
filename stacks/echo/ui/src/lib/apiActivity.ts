// Counts in-flight API requests so the shell can show a progress bar.

let inFlight = 0
const listeners = new Set<() => void>()

function notify() {
  listeners.forEach((fn) => fn())
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function isBusy(): boolean {
  return inFlight > 0
}

export function begin(): void {
  inFlight += 1
  notify()
}

export function end(): void {
  inFlight = Math.max(0, inFlight - 1)
  notify()
}
