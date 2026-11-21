import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  parseWake,
  enableHandsFree,
  disableHandsFree,
  setCommandHandler,
  speakReply,
  restoreHandsFree,
  isEndOfConversation,
  useHandsFree,
} from './handsfree';

/** Stand-in for Chrome's webkitSpeechRecognition. */
class FakeRecognition {
  static instances: FakeRecognition[] = [];
  continuous = false;
  interimResults = true;
  lang = '';
  maxAlternatives = 1;
  started = false;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onresult: ((e: any) => void) | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onerror: ((e: any) => void) | null = null;
  onend: (() => void) | null = null;
  constructor() {
    FakeRecognition.instances.push(this);
  }
  start() {
    this.started = true;
  }
  abort() {
    this.started = false;
  }
  hear(text: string) {
    this.onresult?.({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: true })] });
  }
}

class FakeUtterance {
  text: string;
  lang = '';
  onend: (() => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  constructor(text: string) {
    this.text = text;
  }
}

const spoken: FakeUtterance[] = [];
const latest = () => FakeRecognition.instances[FakeRecognition.instances.length - 1];

beforeEach(() => {
  FakeRecognition.instances = [];
  spoken.length = 0;
  // Tests run in Node: provide the browser globals the module uses.
  vi.stubGlobal('window', globalThis);
  vi.stubGlobal('webkitSpeechRecognition', FakeRecognition);
  vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance);
  vi.stubGlobal('speechSynthesis', {
    speak: (u: FakeUtterance) => spoken.push(u),
    cancel: vi.fn(),
  });
  const mem = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, v),
    removeItem: (k: string) => void mem.delete(k),
  });
});

afterEach(() => {
  disableHandsFree();
  setCommandHandler(null);
  vi.unstubAllGlobals();
});

describe('parseWake', () => {
  it.each([
    ['Hey Nova what is the weather', true, 'what is the weather'],
    ['hi nova, open the camera', true, 'open the camera'],
    ['OK Nova', true, ''],
    ['Hey Jarvis what is the weather', true, 'what is the weather'],
    ['hi jarvis, check my email', true, 'check my email'],
    ['OK Jarvis', true, ''],
    ['hey jervis read my unread mail', true, 'read my unread mail'],
    ['jarvis do this', false, ''],
    ['they said hi to Travis', false, ''],
    ['the supernova was bright', false, ''],
  ])('%s', (text, woke, command) => {
    expect(parseWake(text)).toEqual({ woke, command });
  });
});

describe('hands-free flow', () => {
  it('sends the command after the wake phrase, then speaks the reply and resumes', () => {
    const sent: string[] = [];
    setCommandHandler((c) => {
      sent.push(c);
      return true;
    });
    enableHandsFree();
    expect(useHandsFree.getState().status).toBe('waiting');
    expect(latest().started).toBe(true);

    latest().hear('some background talk');
    expect(sent).toEqual([]);

    latest().hear('Hey Jarvis, do I have unread emails?');
    expect(sent).toEqual(['do I have unread emails?']);
    expect(useHandsFree.getState().status).toBe('thinking');

    speakReply('You have **2** unread emails.');
    expect(useHandsFree.getState().status).toBe('speaking');
    const reply = spoken[spoken.length - 1];
    expect(reply.text).toContain('You have 2 unread emails');

    const before = FakeRecognition.instances.length;
    reply.onend?.();
    // Conversation mode: listening again, no wake word needed.
    expect(useHandsFree.getState().status).toBe('listening');
    expect(useHandsFree.getState().conversation).toBe(true);
    expect(FakeRecognition.instances.length).toBe(before + 1);
    expect(latest().started).toBe(true);
  });

  it('bare wake phrase waits for the next utterance as the command', () => {
    const sent: string[] = [];
    setCommandHandler((c) => {
      sent.push(c);
      return true;
    });
    enableHandsFree();
    latest().hear('Hi Jarvis');
    expect(useHandsFree.getState().status).toBe('listening');
    expect(spoken.some((u) => u.text === 'Yes?')).toBe(true);
    latest().hear('what time is it in Tokyo');
    expect(sent).toEqual(['what time is it in Tokyo']);
  });

  it('says busy instead of sending when a reply is still streaming', () => {
    setCommandHandler(() => false);
    enableHandsFree();
    latest().hear('hey jarvis tell me a joke');
    expect(spoken[spoken.length - 1].text).toMatch(/busy/i);
  });

  it('turns off when the microphone is blocked', () => {
    enableHandsFree();
    latest().onerror?.({ error: 'not-allowed' });
    expect(useHandsFree.getState().status).toBe('off');
    expect(useHandsFree.getState().error).toMatch(/Microphone blocked/);
  });
});

describe('on by default', () => {
  it('starts listening on app open with no tap', () => {
    restoreHandsFree();
    expect(useHandsFree.getState().status).toBe('waiting');
    expect(latest().started).toBe(true);
  });

  it('stays off after the user switches it off', () => {
    enableHandsFree();
    disableHandsFree();
    FakeRecognition.instances = [];
    restoreHandsFree();
    expect(useHandsFree.getState().status).toBe('off');
    expect(FakeRecognition.instances).toHaveLength(0);
    enableHandsFree();
    disableHandsFree({ remember: false });
    restoreHandsFree();
    expect(useHandsFree.getState().status).toBe('waiting');
  });

  it('if the browser needs a tap first, starts on the first tap', () => {
    const listeners: Record<string, () => void> = {};
    vi.stubGlobal('document', {
      visibilityState: 'visible',
      addEventListener: (type: string, fn: () => void) => {
        listeners[type] = fn;
      },
    });
    restoreHandsFree();
    latest().onerror?.({ error: 'not-allowed' });
    expect(useHandsFree.getState().status).toBe('off');
    expect(useHandsFree.getState().error).toMatch(/Tap anywhere once/);
    listeners.pointerdown();
    expect(useHandsFree.getState().status).toBe('waiting');
    expect(latest().started).toBe(true);
  });
});

describe('conversation mode', () => {
  function start() {
    const sent: string[] = [];
    setCommandHandler((c) => {
      sent.push(c);
      return true;
    });
    enableHandsFree();
    latest().hear('Hey Nova, what is the capital of France');
    speakReply('Paris.');
    spoken[spoken.length - 1].onend?.();
    return sent;
  }

  afterEach(() => vi.useRealTimers());

  it('takes follow-ups without the wake word', () => {
    const sent = start();
    latest().hear('and of Germany');
    expect(sent).toEqual(['what is the capital of France', 'and of Germany']);
    speakReply('Berlin.');
    spoken[spoken.length - 1].onend?.();
    latest().hear('hey nova and Italy');
    expect(sent[2]).toBe('and Italy');
  });

  it('ends on "stop" / "thanks Nova" and goes back to needing the wake word', () => {
    const sent = start();
    latest().hear('thanks Nova');
    expect(useHandsFree.getState().status).toBe('waiting');
    expect(useHandsFree.getState().conversation).toBe(false);
    latest().hear('what time is it');
    expect(sent).toHaveLength(1);
  });

  it('times out after silence, but speech in progress keeps it open', () => {
    vi.useFakeTimers();
    start();
    vi.advanceTimersByTime(10000);
    // user starts talking (interim result) just before the window closes
    latest().onresult?.({
      resultIndex: 0,
      results: [Object.assign([{ transcript: 'and' }], { isFinal: false })],
    });
    vi.advanceTimersByTime(10000);
    expect(useHandsFree.getState().status).toBe('listening');
    vi.advanceTimersByTime(3000);
    expect(useHandsFree.getState().status).toBe('waiting');
  });

  it.each([
    ['stop', true],
    ['Okay thanks', true],
    ["that's all", true],
    ['bye Nova', true],
    ['thank you', true],
    ['stop the music', false],
    ['thanks, now play some jazz', false],
  ])('isEndOfConversation(%s)', (text, end) => {
    expect(isEndOfConversation(text)).toBe(end);
  });
});
