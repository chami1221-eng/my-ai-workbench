export type Running = { id: string; tool: string; label: string; startedAt: number; isAgent: boolean }
export type Level = { pct: number; action: string }

declare module 'claude-code' {
  interface PluginState {
    'prompt-band': {
      running: Running[]
      ask: string | null
      level: Level | null
      hiddenLevel: string | null
    }
  }
}
