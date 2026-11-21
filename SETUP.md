# OpenJarvis — local setup (Gemini)

## First run
```bash
uv sync --extra inference-cloud --extra inference-google --extra server --extra dev
cp env.example .env      # then put your key in .env
./jarvis.sh ask "hello"
```

## Swapping the API key
Edit `.env` (gitignored) and change `GEMINI_API_KEY=`. Nothing else needs changing.
To use OpenAI/Anthropic instead, set that key in `.env` and change
`default_model` in `jarvis.toml` (e.g. `gpt-4o-mini`, `claude-sonnet-4-5`).

## Files
| File | Purpose | Committed |
|---|---|---|
| `.env` | API keys | no (gitignored) |
| `env.example` | template for `.env` | yes |
| `jarvis.toml` | engine/model/agent config | yes |
| `jarvis.sh` | runs `jarvis` with `.env` + `jarvis.toml` loaded | yes |

## Common commands
```bash
./jarvis.sh chat                                   # multi-turn chat
./jarvis.sh ask "question"                         # one-shot
./jarvis.sh ask -a orchestrator --tools calculator,code_interpreter,web_search "..."
./jarvis.sh ask -i photo.png "what is in this?"    # vision
./jarvis.sh ask -m gemini-3.1-pro-preview "..."    # override model
./jarvis.sh serve                                  # OpenAI-compatible API on :8000
./jarvis.sh tool list                              # all tools
./jarvis.sh doctor                                 # health check
```

## Gemini notes
- `gemini-2.5-flash` is closed to new API users; `gemini-3.5-flash-lite` is the default
  (sub-second). Full Flash/Pro models work but were slow/overloaded when tested.
- Local patches on top of upstream, both needed for Gemini:
  - `traces/store.py`: Gemini tool calls carry bytes `thought_signature`; trace
    saving crashed on them (tool-using agents failed). Now base64-encoded.
  - `engine/cloud.py`: images (`-i` / `--screen`) were dropped for cloud engines;
    they are now sent to Gemini as inline image parts.
