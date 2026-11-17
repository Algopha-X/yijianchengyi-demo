/** Coalesce edits, serialize requests and retain the latest edit after failures. */
export function createSaveQueue(save: () => Promise<void>) {
  let changed = false
  let active: Promise<void> | undefined
  return {
    mark() { changed = true },
    get dirty() { return changed || !!active },
    async flush(): Promise<void> {
      if (active) { await active; return this.flush() }
      if (!changed) return
      active = (async () => {
        while (changed) {
          changed = false
          try { await save() }
          catch (error) { changed = true; throw error }
        }
      })()
      try { await active } finally { active = undefined }
    },
  }
}
