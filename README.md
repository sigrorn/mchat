# mchat

A multi-provider LLM chat application with a desktop UI. Chat with Claude, GPT, Gemini, Perplexity, and Mistral — using named personas to give each provider a distinct role in the same conversation.

## Features

- **5 providers** — Claude, GPT, Gemini, Perplexity, and Mistral, all in one chat
- **Named personas** — give each provider a role ("Partner", "Critic", "Translator") with its own system prompt, model override, and colour
- **Flexible targeting** — prefix a message with a persona or provider name, use `+name`/`-name`, `all,` for everyone, or `flipped,` for the complement
- **Shared context** — each persona sees the full conversation transcript (filtered by visibility matrix), with other personas' responses labeled as context
- **Persona dialog** — create, edit, and manage personas via a GUI; auto-opens on new chat
- **Export/import** — save and restore persona setups or provider settings as `.md` files
- **Batch rendering** — responses appear fully formatted when complete
- **Markdown formatting** — tables, code blocks, bold, lists rendered inline
- **Column or list layout** — multi-provider responses side by side or stacked
- **Persistent chat history** — conversations saved locally in SQLite
- **Per-persona cost tracking** — estimated spend shown per persona in the toolbar
- **Dynamic model selection** — model lists fetched from APIs, per-persona model override in the toolbar
- **Visibility matrix** — control which persona sees which other persona's responses
- **Context control** — `//limit` to restrict how much history is sent
- **Message editing** — `//edit` to go back and re-send a previous message, replaying subsequent messages
- **Message management** — `//pop` to remove, `//hide`/`//unhide` to temporarily suppress messages
- **Auto-retry** — transient provider errors automatically retried up to 3 times
- **Manual retry** — `//retry` to re-attempt failed requests
- **Configurable system prompt** — per-persona and per-provider
- **HTML export** — save any conversation as a formatted HTML file (Ctrl+S or right-click)
- **Font zoom** — Ctrl+/- to resize, Ctrl+0 to reset
- **Customisable colours** — per-persona and per-provider background colours
- **Message numbers** — user messages show their position for easy reference
- **Debug mode** — `mchat -debug` dumps per-persona provider I/O to timestamped text files

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
- **Mistral**: https://console.mistral.ai/api-keys

On first launch, click Providers in the toolbar to enter your keys. You only need keys for the providers you want to use — unconfigured providers are greyed out.

## Configuration

Toolbar buttons:
- **Personas** — create and manage personas for the current conversation
- **Providers** — API keys, default models, colours, per-provider system prompts (export/import supported)
- **Settings** — font size, shading, global system prompt, user colour

Keys and settings are stored locally in `~/.mchat/config.json`.

## Usage

```bash
mchat          # normal mode
mchat -debug   # writes per-persona I/O logs to <persona-name>.txt
```

### Personas

New chats auto-open the Personas dialog. Create personas with a name, provider, and system prompt:

```
//addpersona claude as "Partner" new Start an Italian conversation
//addpersona openai as "Critic" new Review my replies for mistakes
//addpersona mistral as "Translator" new Word-level translations only
```

Or use `//addpersona` (no args) to open the dialog. Personas can also be exported/imported as `.md` files from the dialog.

### Addressing personas

- `partner, <message>` — send to the "partner" persona
- `claude, <message>` — send to the Claude provider (synthetic default)
- `all, <message>` — send to all personas in the conversation
- `flipped, <message>` — send to non-selected personas and switch selection to them
- `+partner` / `-partner` — add/remove the "partner" persona from the selection
- No prefix — send to current selection (sticky per conversation)

Use the checkboxes in the toolbar, or `//select`.

### Commands

| Command | Description |
|---------|-------------|
| `//edit [N]` | Edit and re-send message N (or last user message) |
| `//edit -N` | Edit the Nth-last user message |
| `//limit <N>` | Only send chat from message N onwards |
| `//limit last` | Limit to the last request sent |
| `//limit ALL` | Remove the limit, send full history |
| `//pop` | Remove the last request and its responses |
| `//hide` | Hide the last request+responses, copy to input |
| `//unhide` | Unhide all hidden messages |
| `//retry` | Re-attempt the last failed request |
| `//select <names>` | Set target personas/providers |
| `//select all` | Target all personas |
| `//providers` | List available providers and config status |
| `//pin <target>, <instr>` | Pin an instruction (bypasses //limit) |
| `//unpin <N>` / `//unpin ALL` | Remove a pin or all pins |
| `//pins [name]` | List pinned instructions (accepts persona or provider name) |
| `//addpersona ...` | Create a persona (no args opens dialog) |
| `//editpersona "<name>" <prompt>` | Update a persona's system prompt |
| `//removepersona "<name>"` | Remove a persona |
| `//personas` | List personas in this chat |
| `//rename <text>` | Rename the current chat |
| `//mode parallel` | Send to all personas simultaneously (default) |
| `//mode sequential` | Send one at a time; each sees prior responses |
| `//visibility separated` | Each persona sees only its own responses |
| `//visibility joined` | Full visibility — everyone sees everyone |
| `//columns` (`//cols`) / `//lines` | Column vs list layout |
| `//help` | Show all commands |
| `//vacuum` | Compact the database (rarely needed) |

### Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+S | Export current chat as HTML |
| Ctrl+= / Ctrl++ | Increase font size |
| Ctrl+- | Decrease font size |
| Ctrl+0 | Reset font size to default |
| Ctrl+F | Find in chat |

### Sidebar

Right-click a conversation for:
- **Rename** — change the conversation title
- **Save as HTML** — export to a formatted HTML file
- **Personas...** — open the persona editor for this conversation
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

### Database maintenance

Chat history and settings are stored in `~/.mchat/` (SQLite database + JSON config). The database is self-maintaining under normal use. If you delete many large conversations and want to reclaim disk space, run `//vacuum` in the chat.

## Development

```bash
# Run tests
pytest

# Run the app
mchat
```
