import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, SwitchCamera, Send, Loader2, Eye } from 'lucide-react';
import { getBase, authHeaders } from '../../lib/api';
import { useAppStore, generateId } from '../../lib/store';
import {
  setCommandHandler,
  speakAnswer,
  replyAborted,
  useHandsFree,
} from '../../lib/handsfree';

/**
 * Live camera: point the phone at something and ask about it (typed, or
 * "Hey Nova, ..." in hands-free mode). Each question captures a few frames
 * over ~1.4 s so the model can see motion, sends them to /v1/vision, shows
 * and speaks the answer, and logs the Q&A in the current chat.
 */

const FRAME_COUNT = 3;
const FRAME_GAP_MS = 700;
const MAX_EDGE = 768;

type Turn = { role: 'user' | 'assistant'; content: string };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function CameraView({ onClose }: { onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [facing, setFacing] = useState<'environment' | 'user'>('environment');
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [asked, setAsked] = useState<string | null>(null);
  const historyRef = useRef<Turn[]>([]);
  const busyRef = useRef(false);
  const handsFree = useHandsFree();

  // Start / restart the camera when the facing mode changes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setCameraError(null);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => {});
        }
      } catch (err) {
        const name = (err as { name?: string })?.name;
        setCameraError(
          name === 'NotAllowedError'
            ? 'Camera blocked. Allow camera access for this app in your browser settings.'
            : 'Could not open the camera.',
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [facing]);

  // Stop the camera when the view closes.
  useEffect(
    () => () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    },
    [],
  );

  const grabFrame = useCallback((): string | null => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return null;
    const scale = Math.min(1, MAX_EDGE / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.75).split(',', 2)[1] ?? null;
  }, []);

  const logToChat = useCallback((q: string, a: string) => {
    const state = useAppStore.getState();
    let convId = state.activeId;
    if (!convId) convId = state.createConversation(state.selectedModel || undefined);
    state.addMessage(convId, { id: generateId(), role: 'user', content: `📷 ${q}`, timestamp: Date.now() });
    state.addMessage(convId, { id: generateId(), role: 'assistant', content: a, timestamp: Date.now() });
  }, []);

  const ask = useCallback(
    async (raw: string, fromVoice = false) => {
      const q = raw.trim() || 'What am I looking at?';
      if (busyRef.current) return false;
      busyRef.current = true;
      setBusy(true);
      setAsked(q);
      setAnswer(null);
      try {
        const frames: string[] = [];
        for (let i = 0; i < FRAME_COUNT; i++) {
          const f = grabFrame();
          if (f) frames.push(f);
          if (i < FRAME_COUNT - 1) await sleep(FRAME_GAP_MS);
        }
        if (!frames.length) throw new Error('The camera is not ready yet.');
        const res = await fetch(`${getBase()}/v1/vision`, {
          method: 'POST',
          headers: authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            question: q,
            images: frames,
            frame_gap_seconds: FRAME_GAP_MS / 1000,
            history: historyRef.current.slice(-6),
          }),
        });
        if (!res.ok) {
          const detail = await res.json().then((d) => d?.detail).catch(() => '');
          throw new Error(
            res.status === 401
              ? 'Not signed in to Nova. Open the app link with your password again.'
              : `Nova couldn't look right now${detail ? `: ${detail}` : ''}.`,
          );
        }
        const data = (await res.json()) as { answer: string };
        setAnswer(data.answer);
        historyRef.current.push({ role: 'user', content: q }, { role: 'assistant', content: data.answer });
        logToChat(q, data.answer);
        speakAnswer(data.answer);
      } catch (err) {
        const msg = (err as Error)?.message || 'Something went wrong.';
        setAnswer(msg);
        if (fromVoice) speakAnswer(msg);
        else replyAborted();
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
      return true;
    },
    [grabFrame, logToChat],
  );

  // While open, "Hey Nova, ..." questions are about what the camera sees.
  useEffect(() => {
    const previous = setCommandHandler((command) => {
      if (/^(close|stop|exit|turn off)( the)? camera\b/i.test(command)) {
        onClose();
        setTimeout(replyAborted, 0); // instant action: back to listening
        return true;
      }
      if (busyRef.current) return false;
      void ask(command, true);
      return true;
    });
    return () => {
      setCommandHandler(previous);
    };
  }, [ask, onClose]);

  const submitTyped = () => {
    const q = question;
    setQuestion('');
    void ask(q);
  };

  const hint =
    handsFree.status === 'waiting'
      ? 'Say “Hey Nova, …” or type a question'
      : handsFree.status === 'listening'
        ? 'Listening…'
        : handsFree.status === 'speaking'
          ? 'Speaking…'
          : 'Ask about what you see';

  // Portal to <body>: the chat column creates its own stacking context, which
  // would let the sidebar toggle draw on top of the camera.
  return createPortal(
    <div className="fixed inset-0 z-[100] flex flex-col bg-black" role="dialog" aria-label="Nova camera">
      <video
        ref={videoRef}
        playsInline
        muted
        autoPlay
        className="absolute inset-0 w-full h-full object-cover"
        style={{ transform: facing === 'user' ? 'scaleX(-1)' : undefined }}
      />

      <div className="relative z-10 flex items-center justify-between p-4" style={{ paddingTop: 'max(16px, env(safe-area-inset-top))' }}>
        <button
          onClick={onClose}
          className="p-2.5 rounded-full bg-black/50 text-white cursor-pointer"
          aria-label="Close camera"
        >
          <X size={22} />
        </button>
        <span className="px-3 py-1 rounded-full bg-black/50 text-white text-xs">{hint}</span>
        <button
          onClick={() => setFacing((f) => (f === 'environment' ? 'user' : 'environment'))}
          className="p-2.5 rounded-full bg-black/50 text-white cursor-pointer"
          aria-label="Switch camera"
        >
          <SwitchCamera size={22} />
        </button>
      </div>

      <div className="relative z-10 flex-1" />

      {cameraError && (
        <div className="relative z-10 mx-4 mb-3 rounded-xl bg-red-600/90 text-white text-sm p-3">{cameraError}</div>
      )}

      {(busy || answer) && (
        <div className="relative z-10 mx-4 mb-3 rounded-2xl bg-black/70 text-white p-4 max-h-[40vh] overflow-y-auto" aria-live="polite">
          {asked && <div className="text-xs opacity-70 mb-1">“{asked}”</div>}
          {busy ? (
            <div className="flex items-center gap-2 text-sm">
              <Loader2 size={16} className="animate-spin" /> Looking…
            </div>
          ) : (
            <div className="text-[15px] leading-relaxed">{answer}</div>
          )}
        </div>
      )}

      <div className="relative z-10 flex items-center gap-2 px-4" style={{ paddingBottom: 'max(16px, env(safe-area-inset-bottom))' }}>
        <button
          onClick={() => void ask('What am I looking at? Explain it briefly.')}
          disabled={busy}
          className="p-3 rounded-full bg-white/90 text-black cursor-pointer disabled:opacity-50 shrink-0"
          aria-label="What is this?"
          title="What is this?"
        >
          <Eye size={20} />
        </button>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submitTyped();
          }}
          placeholder="Ask about what you see…"
          className="flex-1 min-w-0 rounded-full px-4 py-3 bg-white/90 text-black text-[15px] outline-none"
          disabled={busy}
        />
        <button
          onClick={submitTyped}
          disabled={busy}
          className="p-3 rounded-full text-white cursor-pointer disabled:opacity-50 shrink-0"
          style={{ background: 'var(--color-accent)' }}
          aria-label="Ask"
        >
          <Send size={20} />
        </button>
      </div>
    </div>,
    document.body,
  );
}
