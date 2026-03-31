# mchat

A multi-provider LLM chat application with a desktop UI. Chat with Claude and ChatGPT in the same conversation.

## Features

- **Multi-provider conversations** — address providers by name: `claude, explain this code` or `gpt, what do you think?`
- **Both at once** — `both, compare these approaches` sends to Claude and GPT simultaneously
- **Shared context** — each provider sees the full conversation transcript, including other providers' responses
- **Streaming responses** — tokens appear in real-time with incremental markdown rendering
- **Markdown formatting** — tables, code blocks, bold, lists rendered inline
- **Persistent chat history** — conversations saved locally in SQLite
- **Per-conversation cost tracking** — estimated spend shown per provider in the top bar
- **Dynamic model selection** — model lists fetched from APIs, switchable from the top bar
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

## Configuration

On first launch, open Settings (gear icon) to enter your API keys:
- **Anthropic API key** — for Claude models
- **OpenAI API key** — for GPT models

Additional settings:
- **Default provider** — Claude or OpenAI
- **System prompt** — sent at the start of new chats (does not affect existing conversations)
- **Font size** — also adjustable with Ctrl+/- shortcuts
- **Background colours** — for user, Claude, and GPT messages

Keys and settings are stored locally in `~/.mchat/config.json`.

## Usage

```bash
mchat
```

### Addressing providers

- `claude, <your message>` — routes to Claude
- `gpt, <your message>` — routes to ChatGPT
- `both, <your message>` — sends to both simultaneously, renders each response when complete
- No prefix — routes to the last-used provider (sticky per conversation, including "both")

### Cross-provider context

Ask one provider to comment on another's response:
```
you:    gpt, write a haiku about programming
GPT:    Code flows like water / ...
you:    claude, what do you think of that haiku?
Claude: I think it captures...
```

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
```

## Development

```bash
# Run tests
pytest

# Run the app
mchat
```
