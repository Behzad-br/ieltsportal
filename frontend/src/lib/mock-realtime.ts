import { io, type Socket } from 'socket.io-client';
import { socketOrigin } from '@/lib/api-base';
import { getAccessToken } from '@/lib/auth-api';
import { DEMO_MODE } from '@/lib/demo-mode';
import { demoBusPublish, demoMockSocketFacade } from '@/lib/demo-mock-bus';

let socket: Socket | null = null;
let lastJoin: { role: 'student' | 'teacher'; assignmentId: string; studentId?: string } | null = null;
let demoSocket: ReturnType<typeof demoMockSocketFacade> | null = null;

export function mockSocket() {
  if (DEMO_MODE) {
    if (!demoSocket) demoSocket = demoMockSocketFacade();
    return demoSocket as unknown as Socket;
  }
  if (!socket) {
    socket = io(socketOrigin(), {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      autoConnect: true,
      // Backend Socket.IO requires JWT — never trust client-only room join.
      auth: () => ({ token: getAccessToken() }),
    });
    socket.on('connect', () => {
      if (lastJoin) socket?.emit('join_assignment', lastJoin);
    });
  }
  return socket;
}

export function joinMockAssignment(opts: { role: 'student' | 'teacher'; assignmentId: string; studentId?: string }) {
  lastJoin = opts;
  const s = mockSocket();
  if (!DEMO_MODE) {
    if (!s.connected) s.connect();
    s.emit('join_assignment', opts);
  }
  return s;
}

export function emitMockEvent(payload: Record<string, unknown>) {
  if (DEMO_MODE) {
    demoBusPublish({
      ...payload,
      eventType: payload.eventType || payload.event_type,
      event_type: payload.event_type || payload.eventType,
      studentId: payload.studentId || payload.student_id,
      student_id: payload.student_id || payload.studentId,
      attemptId: payload.attemptId || payload.attempt_id,
    });
    return;
  }
  try {
    mockSocket().emit('mock:event', payload);
  } catch {
    /* HTTP heartbeat remains the fallback */
  }
}

export function emitTeacherVoice(opts: {
  assignmentId: string;
  active: boolean;
  studentId?: string;
  attemptId?: string;
}) {
  if (DEMO_MODE) {
    demoBusPublish({
      assignmentId: opts.assignmentId,
      studentId: opts.studentId,
      attemptId: opts.attemptId,
      eventType: opts.active ? 'TEACHER_VOICE_ON' : 'TEACHER_VOICE_OFF',
      event_type: opts.active ? 'TEACHER_VOICE_ON' : 'TEACHER_VOICE_OFF',
    });
    return;
  }
  try {
    mockSocket().emit('teacher:voice', opts);
  } catch {
    /* banner is best-effort; LiveKit audio still works */
  }
}
