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

## Android phone (Samsung Galaxy A56, any Android 7+)
Jarvis runs on the phone itself in Termux; you use it through Chrome as an app.

1. Install **Termux** from F-Droid (https://f-droid.org/packages/com.termux/) or
   its GitHub releases. Not the Play Store version — it is outdated.
   Optional: **Termux:API** and **Termux:Widget** from the same place.
2. In Termux:
   ```bash
   pkg install -y curl
   curl -fsSLO https://raw.githubusercontent.com/138AP831/Externals/master/scripts/android/install-termux.sh
   bash install-termux.sh
   ```
   First install compiles some packages: allow 10–30 min, keep the screen on.
3. Add your key: `nano ~/jarvis/.env` → `GEMINI_API_KEY=...` (Ctrl+O, Enter, Ctrl+X).
4. `jarvis-start` — opens Jarvis in Chrome. In Chrome: ⋮ → **Add to Home screen** →
   **Install** for an app icon.
5. Afterwards: open Termux and run `jarvis-start` (or tap the **Jarvis** widget
   with Termux:Widget), then tap the app icon. `jarvis-stop` stops it.

Samsung battery saver may kill Termux in the background: Settings → Apps → Termux →
Battery → **Unrestricted**. Terminal chat also works: `jarvis chat`.
To update later, re-run `bash install-termux.sh`.

## Phone app (cloud) — recommended
Jarvis runs on a free Render.com server; your phone gets a home-screen app.
No Termux, nothing to start.

1. Sign up at https://render.com (use "Sign in with GitHub").
2. **New → Blueprint** → connect `138AP831/Externals` → branch
   `master` → it reads `render.yaml`.
3. When asked for the two secrets:
   - `OPENJARVIS_API_KEY`: make up a long password (this protects your Jarvis).
   - `GEMINI_API_KEY`: your Google AI Studio key.
4. **Apply**. The first build takes ~10–15 min. Render shows your address,
   e.g. `https://jarvis-ab12.onrender.com`.
5. On the phone, open in Chrome: `https://jarvis-ab12.onrender.com/#key=YOUR_PASSWORD`
   (signs this phone in once; the password is then removed from the address bar).
6. Chrome ⋮ → **Add to Home screen** → **Install**. Done — tap the Jarvis icon.

Free-plan notes: the server sleeps after 15 min unused, so the first message after
a break takes ~1 min; chat history resets when it restarts. Render's $7/month plan
removes both. To change keys: Render dashboard → jarvis → Environment.
