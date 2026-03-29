# mchat

A multi-provider LLM chat application with a desktop UI. Chat with Claude and ChatGPT in the same conversation.

## Features

- **Multi-provider conversations** — address providers by name: `claude, explain this code` or `gpt, what do you think?`
- **Shared context** — each provider sees the full conversation transcript, including other providers' responses
- **Streaming responses** — tokens appear in real-time
- **Persistent chat history** — conversations saved locally in SQLite
- **Claude Desktop-like UI** — clean sidebar + chat layout

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

Keys are stored locally in `~/.mchat/config.json`.

## Usage

```bash
mchat
```

### Addressing providers

- `claude, <your message>` — routes to Claude
- `gpt, <your message>` — routes to ChatGPT
- No prefix — routes to the last-used provider

### Cross-provider context

Ask one provider to comment on another's response:
```
you:    gpt, write a haiku about programming
GPT-4:  Code flows like water / ...
you:    claude, what do you think of that haiku?
Claude: I think it captures...
```

## Development

```bash
# Run tests
pytest

# Run the app
mchat
```
