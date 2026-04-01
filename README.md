# mchat

A multi-provider LLM chat application with a desktop UI. Chat with Claude, GPT, Gemini, and Perplexity in the same conversation.

## Features

- **4 providers** — Claude, GPT, Gemini, and Perplexity, all in one chat
- **Multi-provider targeting** — `//select claude, gpt, gemini` sends to multiple providers simultaneously
- **Shared context** — each provider sees the full conversation transcript, including other providers' responses
- **Streaming responses** — tokens appear in real-time with optional incremental markdown rendering
- **Markdown formatting** — tables, code blocks, bold, lists rendered inline
- **Persistent chat history** — conversations saved locally in SQLite
- **Per-conversation cost tracking** — estimated spend shown per provider in the status bar
- **Dynamic model selection** — model lists fetched from APIs, switchable from the status bar
- **Context marks** — `//mark` and `//limit` to control how much history is sent to providers
- **Configurable system prompt** — snapshotted per conversation at creation time
- **HTML export** — save any conversation as a formatted HTML file (Ctrl+S or right-click)
- **Font zoom** — Ctrl+/- to resize, Ctrl+0 to reset
- **Customisable colours** — per-provider background colours editable in Settings

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
- **System prompt** — sent at the start of new chats (does not affect existing conversations)
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
- No prefix — send to current selection (sticky per conversation)

### Commands

| Command | Description |
|---------|-------------|
| `//select <providers>` | Set target providers (e.g. `//select gpt, claude, gemini`) |
| `//select all` | Target all configured providers |
| `//providers` | List available providers and config status |
| `//mark [tagname]` | Mark this point in the chat |
| `//limit [tagname]` | Only send chat from that mark onwards |
| `//limit ALL` | Remove the limit, send full history |
| `//marks` | List all marks (click to scroll) |
| `//incremental` | Render markdown progressively while streaming |
| `//batch` | Render on completion (default) |
| `//help` | Show all commands |

### Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+S | Export current chat as HTML |
| Ctrl+= / Ctrl++ | Increase font size |
| Ctrl+- | Decrease font size |
| Ctrl+0 | Reset font size to default |

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

## Development

```bash
# Run tests
pytest

# Run the app
mchat
```
