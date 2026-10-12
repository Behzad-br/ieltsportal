/** Cross-tab event bus for DEMO_MODE mock live monitoring (no Socket.IO). */

type Handler = (payload: Record<string, unknown>) => void;

const EVENT = 'mock:state';
const CHANNEL = 'ielts-demo-mock-bus';

const localHandlers = new Set<Handler>();
let channel: BroadcastChannel | null = null;

function ensureChannel() {
  if (typeof window === 'undefined') return null;
  if (channel) return channel;
  try {
    channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = (msg) => {
      const payload = (msg.data || {}) as Record<string, unknown>;
      localHandlers.forEach((fn) => fn(payload));
    };
  } catch {
    channel = null;
  }
  return channel;
}

export function demoBusPublish(payload: Record<string, unknown>) {
  localHandlers.forEach((fn) => fn(payload));
  try {
    ensureChannel()?.postMessage(payload);
  } catch {
    /* ignore */
  }
}

export function demoBusSubscribe(handler: Handler) {
  ensureChannel();
  localHandlers.add(handler);
  return () => {
    localHandlers.delete(handler);
  };
}

/** Minimal socket-shaped facade used by LiveMonitoringPage / MockRoom in DEMO_MODE. */
export function demoMockSocketFacade() {
  const wrappers = new Map<Handler, Handler>();
  return {
    connected: true,
    connect() {},
    emit(_event: string, _payload?: unknown) {},
    on(event: string, handler: Handler) {
      if (event !== EVENT) return;
      const wrap: Handler = (payload) => handler(payload);
      wrappers.set(handler, wrap);
      demoBusSubscribe(wrap);
    },
    off(event: string, handler: Handler) {
      if (event !== EVENT) return;
      const wrap = wrappers.get(handler);
      if (wrap) {
        localHandlers.delete(wrap);
        wrappers.delete(handler);
      }
    },
  };
}
