import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Storage helpers.
 *
 * Every one of these swallows its own errors, because `localStorage` is not
 * always there to be written to — a browser in private mode, or with site data
 * blocked, throws on `getItem` as readily as on `setItem`. A preference failing
 * to persist is a shrug; a preference taking the page down with it is not.
 *
 * `store` is a parameter rather than a hard-coded `localStorage` so callers can
 * choose the lifetime: preferences belong in local storage, cursors that should
 * die with the tab belong in session storage.
 */

export function readJSON<T>(key: string, fallback: T, store: Storage = localStorage): T {
  try {
    const raw = store.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

export function writeJSON(key: string, value: unknown, store: Storage = localStorage): void {
  try {
    store.setItem(key, JSON.stringify(value))
  } catch {
    /* storage unavailable — the preference just doesn't persist */
  }
}

/** A once-only flag, stored as the literal string "true". */
export function readFlag(key: string, store: Storage = localStorage): boolean {
  try {
    return store.getItem(key) === 'true'
  } catch {
    return false
  }
}

export function writeFlag(key: string, store: Storage = localStorage): void {
  try {
    store.setItem(key, 'true')
  } catch {
    /* see above */
  }
}
