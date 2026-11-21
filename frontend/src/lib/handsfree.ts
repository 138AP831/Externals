import { create } from 'zustand';
import { toSpeakableText } from './message-text';

/**
 * Hands-free voice mode: listens for "Hey Jarvis" / "Hi Jarvis" using the
 * browser's own speech recognition (Chrome on Android uses Google's), sends
 * the spoken command as a chat message, and reads the reply aloud with the
 * browser's speech synthesis. Needs no speech backend on the server.
 *
 * Works only while the app is open and on screen: browsers stop microphone
 * access for pages in the background.
 */

export type HandsFreeStatus =
  | 'off'
  | 'unsupported'
  | 'waiting' // listening for the wake phrase
  | 'listening' // woke up, waiting for the command
  | 'thinking' // command sent, waiting for the reply
  | 'speaking';

// Speech recognisers often mishear "Jarvis"; accept the common variants.
const WAKE_RE =
  /\b(?:hey|hi|hai|hay|hello|ok|okay)[\s,]+(?:jarvis|jarvas|jervis|javis|jarvi|jaavis)\b[\s,.!?]*(.*)$/i;

/** Split a transcript into wake phrase + command. */
export function parseWake(transcript: string): { woke: boolean; command: string } {
  const m = transcript.trim().match(WAKE_RE);
  if (!m) return { woke: false, command: '' };
  return { woke: true, command: (m[1] || '').trim() };
}

const STORAGE_KEY = 'openjarvis-handsfree';
const COMMAND_WINDOW_MS = 8000;

interface HandsFreeStore {
  status: HandsFreeStatus;
  heard: string;
  error: string | null;
  set: (patch: Partial<Pick<HandsFreeStore, 'status' | 'heard' | 'error'>>) => void;
}

export const useHandsFree = create<HandsFreeStore>((set) => ({
  status: 'off',
  heard: '',
  error: null,
  set: (patch) => set(patch),
}));

type CommandHandler = (command: string) => boolean;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Recognition = any;

let recognition: Recognition | null = null;
let enabled = false;
let commandHandler: CommandHandler | null = null;
let commandTimer: ReturnType<typeof setTimeout> | null = null;
let restartTimer: ReturnType<typeof setTimeout> | null = null;
let quickFailures = 0;
let lastStart = 0;

const store = () => useHandsFree.getState();

function recognitionCtor(): (new () => Recognition) | null {
  if (typeof window === 'undefined') return null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function isHandsFreeSupported(): boolean {
  return !!recognitionCtor() && typeof window !== 'undefined' && 'speechSynthesis' in window;
}

/** Register the function that sends a command as a chat message. */
export function setCommandHandler(handler: CommandHandler | null): void {
  commandHandler = handler;
}

function clearCommandTimer() {
  if (commandTimer) {
    clearTimeout(commandTimer);
    commandTimer = null;
  }
}

function submit(command: string) {
  clearCommandTimer();
  const sent = commandHandler ? commandHandler(command) : false;
  if (sent) {
    store().set({ status: 'thinking', heard: command });
    stopRecognition(); // don't listen while Jarvis thinks and talks
  } else {
    say("Sorry, I'm still busy with the last request.");
  }
}

function onTranscript(text: string) {
  const { status } = store();
  if (status === 'listening') {
    if (text.trim()) submit(text.trim());
    return;
  }
  if (status !== 'waiting') return;
  const { woke, command } = parseWake(text);
  if (!woke) return;
  if (command) {
    submit(command);
  } else {
    store().set({ status: 'listening', heard: '' });
    chime();
    clearCommandTimer();
    commandTimer = setTimeout(() => {
      if (store().status === 'listening') store().set({ status: 'waiting' });
    }, COMMAND_WINDOW_MS);
  }
}

function startRecognition() {
  const Ctor = recognitionCtor();
  if (!Ctor || !enabled || recognition) return;
  const status = store().status;
  if (status === 'thinking' || status === 'speaking') return;
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;

  const rec: Recognition = new Ctor();
  rec.continuous = true;
  rec.interimResults = false;
  rec.lang = navigator.language || 'en-US';
  rec.maxAlternatives = 1;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  rec.onresult = (event: any) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) onTranscript(result[0].transcript || '');
    }
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  rec.onerror = (event: any) => {
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      disableHandsFree();
      store().set({
        error: 'Microphone blocked. Allow microphone access for this app, then turn hands-free on again.',
      });
    }
  };
  rec.onend = () => {
    recognition = null;
    if (!enabled) return;
    // Chrome ends continuous recognition after silence; keep it going, but
    // back off if it keeps failing immediately (e.g. no network).
    quickFailures = Date.now() - lastStart < 1500 ? quickFailures + 1 : 0;
    const delay = quickFailures > 3 ? 5000 : 250;
    if (restartTimer) clearTimeout(restartTimer);
    restartTimer = setTimeout(startRecognition, delay);
  };
  recognition = rec;
  lastStart = Date.now();
  try {
    rec.start();
  } catch {
    recognition = null;
  }
}

function stopRecognition() {
  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }
  const rec = recognition;
  recognition = null;
  if (rec) {
    rec.onend = null;
    try {
      rec.abort();
    } catch {
      /* already stopped */
    }
  }
}

function backToWaiting() {
  if (!enabled) return;
  store().set({ status: 'waiting' });
  startRecognition();
}

function chime() {
  say('Yes?', { resumeAfter: false });
}

function say(text: string, opts: { resumeAfter?: boolean } = {}) {
  const resumeAfter = opts.resumeAfter ?? true;
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
    if (resumeAfter) backToWaiting();
    return;
  }
  const synth = window.speechSynthesis;
  synth.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = navigator.language || 'en-US';
  if (resumeAfter) {
    store().set({ status: 'speaking' });
    stopRecognition();
    utter.onend = () => backToWaiting();
    utter.onerror = (e: SpeechSynthesisErrorEvent) => {
      if (e.error === 'not-allowed') {
        store().set({ error: 'Tap the screen once to allow Jarvis to speak.' });
      }
      backToWaiting();
    };
  }
  synth.speak(utter);
}

/** Read a finished reply aloud, then go back to waiting for "Hey Jarvis". */
export function speakReply(text: string): void {
  if (!enabled) return;
  const speakable = toSpeakableText(text).trim();
  if (!speakable) {
    backToWaiting();
    return;
  }
  say(speakable);
}

/** The reply failed or was cancelled: resume listening. */
export function replyAborted(): void {
  if (enabled && store().status === 'thinking') backToWaiting();
}

/** Stop talking now (e.g. user tapped the status pill). */
export function stopSpeaking(): void {
  if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  if (store().status === 'speaking') backToWaiting();
}

export function enableHandsFree(): void {
  if (!isHandsFreeSupported()) {
    store().set({ status: 'unsupported', error: 'Voice mode needs Chrome on Android or desktop.' });
    return;
  }
  enabled = true;
  try {
    localStorage.setItem(STORAGE_KEY, '1');
  } catch {
    /* storage blocked */
  }
  // Called from a tap: unlock speech output for this session.
  try {
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(''));
  } catch {
    /* ignore */
  }
  store().set({ status: 'waiting', error: null });
  startRecognition();
}

export function disableHandsFree(): void {
  enabled = false;
  clearCommandTimer();
  stopRecognition();
  try {
    window.speechSynthesis?.cancel();
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  store().set({ status: 'off' });
}

export function isHandsFreeEnabled(): boolean {
  return enabled;
}

/** Re-enable on app start if the user left hands-free on last time. */
export function restoreHandsFree(): void {
  let wanted = false;
  try {
    wanted = localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    /* ignore */
  }
  if (!wanted || !isHandsFreeSupported()) return;
  enabled = true;
  store().set({ status: 'waiting', error: null });
  startRecognition();
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (!enabled) return;
    if (document.visibilityState === 'visible') {
      if (store().status === 'waiting' || store().status === 'listening') startRecognition();
    } else {
      stopRecognition();
    }
  });
  // First tap after an automatic start unlocks speech output.
  document.addEventListener(
    'pointerdown',
    () => {
      if (store().error?.startsWith('Tap the screen')) store().set({ error: null });
    },
    { capture: true },
  );
}
