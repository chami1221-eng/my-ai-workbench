export type Shot = { path: string; label: string }

declare module 'claude-code' {
  interface PluginState {
    'shot-peek': { shots: Shot[]; isHidden: boolean }
  }
}
