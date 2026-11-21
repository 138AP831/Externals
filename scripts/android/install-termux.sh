#!/data/data/com.termux/files/usr/bin/bash
# OpenJarvis installer for Android (Termux). Tested target: Samsung Galaxy A56.
#
# In Termux, run:
#   curl -fsSLO https://raw.githubusercontent.com/138AP831/Externals/master/scripts/android/install-termux.sh
#   bash install-termux.sh
#
# Re-running is safe: it updates the checkout and reinstalls.
set -euo pipefail

REPO_URL="${JARVIS_REPO_URL:-https://github.com/138AP831/Externals.git}"
BRANCH="${JARVIS_BRANCH:-master}"
DIR="${JARVIS_DIR:-$HOME/jarvis}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }

if [ -z "${TERMUX_VERSION:-}" ] && [ ! -d /data/data/com.termux ]; then
  warn "This script is for Termux on Android. Install Termux from F-Droid or GitHub (not the Play Store version)."
  exit 1
fi

say "Updating Termux packages"
yes | pkg update -y || true
pkg upgrade -y -o Dpkg::Options::=--force-confnew || true

say "Installing build tools"
pkg install -y python git clang make cmake rust binutils pkg-config \
  libffi openssl libxml2 libxslt zlib libjpeg-turbo termux-api

# Prebuilt Termux packages for dependencies that have no Android wheels on
# PyPI. Each is optional: if a name is missing, pip compiles it instead
# (slower, but works because clang + rust are installed above).
say "Installing prebuilt Python packages (some may be unavailable — that's OK)"
for p in python-numpy python-pyarrow python-pandas python-cryptography \
         python-lxml python-pydantic-core python-psutil python-pillow; do
  pkg install -y "$p" >/dev/null 2>&1 && echo "  ok   $p" || echo "  skip $p (will build from source)"
done

PYV="$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$PYV" in
  3.11|3.12|3.13) ;;
  *) warn "Termux Python is $PYV; OpenJarvis supports 3.11–3.13. Continuing, but install may fail." ;;
esac

say "Getting OpenJarvis into $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$DIR" checkout -B "$BRANCH" FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$DIR"
fi
cd "$DIR"

say "Creating Python environment"
# --system-site-packages lets the venv reuse Termux's prebuilt packages.
[ -d .venv ] || python -m venv --system-site-packages .venv
# Needed by maturin/pyo3 when a Rust package (e.g. pydantic-core) compiles.
export ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-$(getprop ro.build.version.sdk 2>/dev/null || echo 24)}"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}"
.venv/bin/python -m pip install --upgrade pip wheel setuptools >/dev/null

say "Installing OpenJarvis (first run can take 10–30 min while packages compile)"
.venv/bin/python -m pip install -e ".[inference-google,inference-cloud,server]"

if [ ! -f .env ]; then
  cp env.example .env
  chmod 600 .env
fi

# Home-screen launchers (needs the Termux:Widget app) and a `jarvis-start` command.
mkdir -p "$HOME/.shortcuts" "$PREFIX/bin"
cat > "$PREFIX/bin/jarvis-start" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
# Start the Jarvis server and open it in the browser.
cd "$DIR"
termux-wake-lock 2>/dev/null || true
if ! curl -fs http://127.0.0.1:8000/health >/dev/null 2>&1; then
  nohup ./jarvis.sh serve --host 127.0.0.1 --port 8000 > "$DIR/serve.log" 2>&1 &
  for i in \$(seq 1 60); do
    curl -fs http://127.0.0.1:8000/health >/dev/null 2>&1 && break
    sleep 1
  done
fi
termux-open-url http://127.0.0.1:8000/ 2>/dev/null || echo "Open http://127.0.0.1:8000 in Chrome"
EOF
cat > "$PREFIX/bin/jarvis-stop" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
pkill -f "jarvis serve" && echo "Jarvis stopped." || echo "Jarvis was not running."
termux-wake-unlock 2>/dev/null || true
EOF
cat > "$PREFIX/bin/jarvis" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec "$DIR/jarvis.sh" "\$@"
EOF
chmod +x "$PREFIX/bin/jarvis-start" "$PREFIX/bin/jarvis-stop" "$PREFIX/bin/jarvis"
cp "$PREFIX/bin/jarvis-start" "$HOME/.shortcuts/Jarvis"
cp "$PREFIX/bin/jarvis-stop"  "$HOME/.shortcuts/Jarvis Stop"
chmod +x "$HOME/.shortcuts/"*

say "Checking install"
./.venv/bin/jarvis --version

if grep -qE '^GEMINI_API_KEY=.+' .env; then
  say "Done! Run:  jarvis-start"
else
  say "Almost done — add your Gemini key:"
  echo "    nano $DIR/.env        # set GEMINI_API_KEY=..."
  echo "  then run:  jarvis-start"
fi
cat <<'EOF'

  jarvis-start          start Jarvis and open it in Chrome
  jarvis-stop           stop it
  jarvis chat           chat in the terminal instead

  In Chrome: menu (⋮) > "Add to Home screen" > Install  → Jarvis app icon.
EOF
