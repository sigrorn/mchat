# mchat

A multi-provider LLM chat application with a desktop UI. Chat with Claude, GPT, Gemini, and Perplexity in the same conversation.

## Features

- **4 providers** — Claude, GPT, Gemini, and Perplexity, all in one chat
- **Flexible targeting** — prefix a message with a provider name, use `//select` for multi-provider, or `all,` for everyone
- **Shared context** — each provider sees the full conversation transcript, including other providers' responses
- **Batch rendering** — responses appear fully formatted when complete
- **Markdown formatting** — tables, code blocks, bold, lists rendered inline
- **Column or list layout** — multi-provider responses side by side or stacked
- **Persistent chat history** — conversations saved locally in SQLite
- **Per-conversation cost tracking** — estimated spend shown per provider
- **Dynamic model selection** — model lists fetched from APIs, switchable from the status bar
- **Context control** — `//limit` to restrict how much history is sent to providers
- **Message management** — `//pop` to remove, `//hide`/`//unhide` to temporarily suppress messages
- **Auto-retry** — transient provider errors automatically retried up to 3 times
- **Manual retry** — `//retry` to re-attempt failed requests
- **Configurable system prompt** — per-conversation and per-provider
- **HTML export** — save any conversation as a formatted HTML file (Ctrl+S or right-click)
- **Font zoom** — Ctrl+/- to resize, Ctrl+0 to reset
- **Customisable colours** — per-provider background colours editable in Settings
- **Message numbers** — user messages show their position for easy reference with `//limit`

## Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install
pip install -e ".[dev]"
```

## API Keys

Get your API keys from:
- **Anthropic (Claude)**: https://console.anthropic.com/
- **OpenAI (GPT)**: https://platform.openai.com/api-keys
- **Google (Gemini)**: https://aistudio.google.com/apikey
- **Perplexity**: https://www.perplexity.ai/settings/api

On first launch, open Settings (gear icon) to enter your keys. You only need keys for the providers you want to use — unconfigured providers are greyed out.

## Configuration

Settings (gear icon or via config file):
- **API keys** — one per provider
- **Default provider** — which provider receives messages by default
- **Default model** — per provider, also switchable from the status bar
- **System prompt** — sent at the start of new chats (snapshotted per conversation)
- **Provider-specific prompts** — additional instructions per provider (always uses current config)
- **Font size** — also adjustable with Ctrl+/- shortcuts
- **Background colours** — per-provider and user message colours

Keys and settings are stored locally in `~/.mchat/config.json`.

## Usage

```bash
mchat
```

### Addressing providers

- `claude, <message>` — send to Claude
- `gpt, <message>` — send to GPT
- `gemini, <message>` — send to Gemini
- `perplexity, <message>` — send to Perplexity (also: `pplx,`)
- `all, <message>` — send to all configured providers
- No prefix — send to current selection (sticky per conversation)

Use the checkboxes in the status bar to select multiple providers, or `//select`.

### Commands

| Command | Description |
|---------|-------------|
| `//limit <N>` | Only send chat from message N onwards |
| `//limit last` | Limit to the last request sent to providers |
| `//limit ALL` | Remove the limit, send full history |
| `//pop` | Remove the last request and its responses |
| `//hide` | Hide the last request+responses, copy request to input |
| `//unhide` | Unhide all hidden messages |
| `//retry` | Re-attempt the last failed request |
| `//select <providers>` | Set target providers (e.g. `//select gpt, claude`) |
| `//select all` | Target all configured providers |
| `//providers` | List available providers and config status |
| `//columns` (`//cols`) | Show multi-provider responses side by side |
| `//lines` | Show multi-provider responses as a list (default) |
| `//help` | Show all commands |

### Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+S | Export current chat as HTML |
| Ctrl+= / Ctrl++ | Increase font size |
| Ctrl+- | Decrease font size |
| Ctrl+0 | Reset font size to default |

### Sidebar

Right-click a conversation for:
- **Rename** — change the conversation title
- **Save as HTML** — export to a formatted HTML file
- **Delete** — remove the conversation

### Copying text

Select any range of text in the chat and copy (Ctrl+C). Speaker transitions are automatically prefixed in the clipboard:
```
//user
What is the capital of France?
//claude (sonnet-4)
The capital of France is Paris.
//gemini (2.5-flash)
Paris is the capital of France.
```

Pasting text with these prefixes into the input box automatically strips them.

## Development

```bash
# Run tests
pytest

# Run the app
mchat
```
