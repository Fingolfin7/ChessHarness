const PROVIDER_LABELS = {
  openai_chatgpt: 'OPENAI_CODEX',
  copilot_chat: 'GITHUB_COPILOT',
}

export function providerLabel(provider) {
  return PROVIDER_LABELS[provider] || provider
}

const PROVIDER_ORDER = [
  'openai_chatgpt',
  'copilot_chat',
  'openai',
  'google',
  'anthropic',
  'openrouter',
  'kimi',
]

const PROVIDER_RANK = Object.fromEntries(
  PROVIDER_ORDER.map((name, index) => [name, index]),
)

export function compareProviderNames(a, b) {
  const ra = PROVIDER_RANK[a]
  const rb = PROVIDER_RANK[b]
  if (ra !== undefined && rb !== undefined) return ra - rb
  if (ra !== undefined) return -1
  if (rb !== undefined) return 1
  return a.localeCompare(b)
}
