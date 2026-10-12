import { DEMO_MODE } from '@/lib/demo-mode';
import { getAuthUser } from '@/lib/auth-api';
import { getCurrentBranchAdmin, getCurrentStudent, getCurrentTeacher } from '@/lib/mock-api';
import {
  demoBranchAttendanceOverview,
  demoFindSession,
  demoListAttendanceSessions,
  demoSaveAttendanceSession,
  demoStudentAttendance,
  type AttendanceMark,
  type AttendanceSession,
  type AttendanceStatus,
} from '@/lib/demo-attendance-store';

export type { AttendanceMark, AttendanceSession, AttendanceStatus };

export function listTeacherAttendance(batch?: string) {
  if (DEMO_MODE) {
    const teacher = getCurrentTeacher();
    return Promise.resolve({
      sessions: demoListAttendanceSessions({
        teacherId: teacher.id || undefined,
        batch: batch || undefined,
      }),
    });
  }
  return Promise.resolve({ sessions: [] as AttendanceSession[] });
}

export function getAttendanceForDate(date: string, batch: string) {
  if (DEMO_MODE) {
    const teacher = getCurrentTeacher();
    return Promise.resolve({
      session: demoFindSession(date, batch, teacher.id || ''),
    });
  }
  return Promise.resolve({ session: null as AttendanceSession | null });
}

export function saveAttendanceSession(body: {
  id?: string;
  date: string;
  batch: string;
  notes?: string;
  marks: Record<string, AttendanceMark>;
}) {
  if (DEMO_MODE) {
    const teacher = getCurrentTeacher();
    const user = getAuthUser();
    const saved = demoSaveAttendanceSession({
      id: body.id,
      date: body.date,
      batch: body.batch,
      teacherId: teacher.id || user?.id || 'demo-teacher-1',
      teacherName: teacher.fullName || teacher.name || user?.full_name || 'Teacher',
      branchId: teacher.branchId || user?.branch_id || user?.branchId || 'demo-gujranwala',
      notes: body.notes,
      marks: body.marks,
    });
    return Promise.resolve({ session: saved });
  }
  return Promise.reject(new Error('Attendance API is only available in demo mode right now.'));
}

export function studentMyAttendance() {
  if (DEMO_MODE) {
    const me = getCurrentStudent();
    return Promise.resolve(demoStudentAttendance(me.id));
  }
  return Promise.resolve({
    studentId: '',
    records: [] as ReturnType<typeof demoStudentAttendance>['records'],
    summary: { present: 0, absent: 0, late: 0, excused: 0, total: 0, rate: 0 },
  });
}

export function branchAttendanceOverview() {
  if (DEMO_MODE) {
    const campus = getCurrentBranchAdmin();
    const branchId = campus?.id || getAuthUser()?.branch_id || 'demo-gujranwala';
    return Promise.resolve(demoBranchAttendanceOverview(branchId));
  }
  return Promise.resolve({
    sessions: [] as AttendanceSession[],
    students: [] as ReturnType<typeof demoBranchAttendanceOverview>['students'],
  });
}
