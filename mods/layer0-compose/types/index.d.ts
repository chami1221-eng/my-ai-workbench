// Read by hook-daemon (it answers UserPromptSubmit from the outside, so it strips what this mod cannot)
export type Layer0Composed = string | null

declare module 'claude-code' {
  interface PluginState {
    'layer0-compose': {
      // The Layer 0 text placed in the system prompt, or null if none
      composed: Layer0Composed
      // Loaded at session startup? (if loaded mid-session the section does not reach the main conversation,
      // so the injected copy must NOT be stripped)
      startup: boolean
    }
  }
}
