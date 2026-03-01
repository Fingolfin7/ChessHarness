const PROVIDER_LABELS = {
  openai_chatgpt: 'OPENAI_CODEX',
  copilot_chat: 'GITHUB_COPILOT',
}

export function providerLabel(provider) {
  return PROVIDER_LABELS[provider] || provider
}

