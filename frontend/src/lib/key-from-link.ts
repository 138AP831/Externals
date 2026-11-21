// One-tap sign-in for a password-protected (OPENJARVIS_API_KEY) server:
// opening  https://host/#key=SECRET  saves SECRET as the API key on this device
// and strips it from the address bar. The URL fragment is never sent to the
// server, so the key does not appear in request logs. Imported before the
// store so the saved key is picked up on first render.
try {
  const match = window.location.hash.match(/(?:^#|&)key=([^&]+)/);
  if (match) {
    const key = decodeURIComponent(match[1]);
    const raw = localStorage.getItem('openjarvis-settings');
    const settings = raw ? JSON.parse(raw) : {};
    settings.apiKey = key;
    localStorage.setItem('openjarvis-settings', JSON.stringify(settings));
    window.history.replaceState(
      null,
      '',
      window.location.pathname + window.location.search,
    );
  }
} catch {
  // Storage blocked (private mode): the key can still be entered in Settings.
}
