/**
 * Developer sandbox only — never the staging/production source of truth.
 *
 * Opt-in ONLY via VITE_DEMO_MODE=true.
 * Default (unset / false / staging builds): API + PostgreSQL only.
 * Do NOT auto-enable in Vite DEV — that caused localStorage dual-SoT.
 */
export const DEMO_MODE = (() => {
  const flag = (import.meta.env.VITE_DEMO_MODE as string | undefined)?.trim().toLowerCase();
  return flag === 'true';
})();

export const DEMO_BANNER_TEXT = 'DEMO DATA - NOT PERSISTED | local sandbox only';

const DEMO_TOKEN = 'demo-local-token';
/** Bump when seed shape changes so local browsers reseed. */
const DEMO_SEEDED_KEY = 'ielts-demo-seeded-v2';
const DEMO_BATCH_A = 'Morning Batch A';
const DEMO_BATCH_B = 'Morning Batch B';
const DEMO_BATCH_EVE = 'Evening Batch A';
const DEMO_HQ = 'demo-gujranwala';

/** When demo is off, drop any leftover demo-local-token session so staging uses real JWT only. */
export function purgeDemoSessionIfDisabled() {
  if (DEMO_MODE || typeof window === 'undefined') return;
  try {
    for (const store of [sessionStorage, localStorage]) {
      const token = store.getItem('ielts-access-token') || '';
      if (token === DEMO_TOKEN) {
        store.removeItem('ielts-access-token');
        store.removeItem('ielts-auth-user');
        store.removeItem('ielts-role');
        store.removeItem('ielts-current-teacher-id');
        store.removeItem('ielts-current-student');
        store.removeItem('writing_student_id');
        store.removeItem('ielts-branch-admin-id');
        store.removeItem('ielts-branch-admin-username');
      }
    }
  } catch {
    /* ignore */
  }
}

function randInt(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function pick<T>(arr: T[]): T {
  return arr[randInt(0, arr.length - 1)];
}

const FIRST = ['Ali', 'Sara', 'Hassan', 'Ayesha', 'Omar', 'Fatima', 'Bilal', 'Zara', 'Usman', 'Hina'];
const LAST = ['Khan', 'Ahmed', 'Raza', 'Malik', 'Hussain', 'Iqbal', 'Sheikh', 'Butt', 'Mirza', 'Qureshi'];
const CITIES = ['Lahore', 'Karachi', 'Islamabad', 'Gujranwala', 'Faisalabad', 'Multan', 'Rawalpindi'];

export function randomPerson() {
  const first = pick(FIRST);
  const last = pick(LAST);
  const fullName = `${first} ${last}`;
  const username = `${first.toLowerCase()}.${last.toLowerCase()}${randInt(10, 99)}`;
  const email = `${username}@demo.fti.local`;
  const password = `Demo${randInt(100000, 999999)}!`;
  return { fullName, first, last, username, email, password };
}

export function randomCampusName() {
  return `${pick(['FTI', 'FTI Hub', 'FTI Centre'])} ${pick(CITIES)} ${pick(['Main', 'North', 'City', 'Plaza'])}`;
}

/** Fake JWT session so route guards + authHeaders pass in demo mode. */
export function ensureDemoSession(role: 'super_admin' | 'branch_admin' | 'teacher' | 'student', extra?: Record<string, string>) {
  const person = randomPerson();
  const user = {
    id: extra?.id || `demo-${role}-${randInt(1000, 9999)}`,
    role,
    email: extra?.email || person.email,
    username: extra?.username || person.username,
    full_name: extra?.full_name || person.fullName,
    name: extra?.full_name || person.fullName,
    branch_id: extra?.branch_id || DEMO_HQ,
    branchId: extra?.branch_id || DEMO_HQ,
    batch: extra?.batch || DEMO_BATCH_A,
    batches: extra?.batches || `${DEMO_BATCH_A}, ${DEMO_BATCH_EVE}`,
    is_active: true,
  };
  sessionStorage.setItem('ielts-access-token', DEMO_TOKEN);
  sessionStorage.setItem('ielts-auth-user', JSON.stringify(user));
  return user;
}

export function isDemoToken(token: string) {
  return token === DEMO_TOKEN;
}

/** Seed branches / teachers / students into localStorage once. */
export function seedDemoWorld() {
  if (typeof window === 'undefined') return;
  if (localStorage.getItem(DEMO_SEEDED_KEY) === '1' && localStorage.getItem('ielts-branches-v1')) {
    return;
  }

  const branches = [
    {
      id: 'demo-gujranwala',
      name: 'FTI Head Office Gujranwala',
      city: 'Gujranwala',
      students: 42,
      teachers: 6,
      avg: 6.4,
      admin: 'Nadia Admin',
      adminUsername: 'nadia.admin',
      adminPassword: 'DemoAdmin1!',
      admins: [{ name: 'Nadia Admin', username: 'nadia.admin', password: 'DemoAdmin1!' }],
    },
    {
      id: 'demo-lahore',
      name: 'FTI Lahore Mall',
      city: 'Lahore',
      students: 35,
      teachers: 5,
      avg: 6.2,
      admin: 'Omar Admin',
      adminUsername: 'omar.admin',
      adminPassword: 'DemoAdmin2!',
      admins: [{ name: 'Omar Admin', username: 'omar.admin', password: 'DemoAdmin2!' }],
    },
    {
      id: 'demo-islamabad',
      name: 'FTI Islamabad Centre',
      city: 'Islamabad',
      students: 28,
      teachers: 4,
      avg: 6.5,
      admin: 'Sara Admin',
      adminUsername: 'sara.admin',
      adminPassword: 'DemoAdmin3!',
      admins: [{ name: 'Sara Admin', username: 'sara.admin', password: 'DemoAdmin3!' }],
    },
  ];

  // Teacher 0 + most students share Morning Batch A at HQ so Assign mock → inbox works.
  const teachers = Array.from({ length: 6 }, (_, i) => {
    const p = randomPerson();
    const isLead = i === 0;
    const isEve = i === 1;
    return {
      id: `demo-teacher-${i + 1}`,
      fullName: isLead ? 'Demo Teacher' : p.fullName,
      name: isLead ? 'Demo Teacher' : p.fullName,
      email: isLead ? 'teacher@demo.fti.local' : p.email,
      username: isLead ? 'teacher.demo' : p.username,
      password: isLead ? 'DemoTeacher1!' : p.password,
      batches: isLead ? DEMO_BATCH_A : isEve ? DEMO_BATCH_EVE : pick([DEMO_BATCH_B, DEMO_BATCH_EVE]),
      branchId: isLead || isEve ? DEMO_HQ : pick(branches).id,
      branchName: '',
    };
  });

  const students = Array.from({ length: 12 }, (_, i) => {
    const p = randomPerson();
    const inBatchA = i < 8;
    return {
      id: `demo-student-${i + 1}`,
      fullName: i === 0 ? 'Demo Student' : p.fullName,
      name: i === 0 ? 'Demo Student' : p.fullName,
      email: i === 0 ? 'student@demo.fti.local' : p.email,
      username: i === 0 ? 'student.demo' : p.username,
      password: i === 0 ? 'DemoStudent1!' : p.password,
      batch: inBatchA ? DEMO_BATCH_A : pick([DEMO_BATCH_B, DEMO_BATCH_EVE]),
      band: String((5.5 + Math.random() * 2).toFixed(1)),
      branchId: DEMO_HQ,
      branchName: '',
      status: 'Active',
      lastActive: 'Today',
    };
  });

  const batches = [
    {
      id: 'demo-batch-1',
      name: DEMO_BATCH_A,
      schedule: 'Mon · Wed · Fri',
      subjects: 'IELTS Academic',
      branchId: DEMO_HQ,
      teacherId: teachers[0]?.id || '',
      teacherName: teachers[0]?.fullName || '',
      students: String(students.filter((s) => s.batch === DEMO_BATCH_A).length),
      avg: '6.6',
    },
    {
      id: 'demo-batch-2',
      name: DEMO_BATCH_EVE,
      schedule: 'Tue · Thu',
      subjects: 'IELTS General',
      branchId: DEMO_HQ,
      teacherId: teachers[1]?.id || '',
      teacherName: teachers[1]?.fullName || '',
      students: String(students.filter((s) => s.batch === DEMO_BATCH_EVE).length),
      avg: '6.1',
    },
    {
      id: 'demo-batch-3',
      name: DEMO_BATCH_B,
      schedule: 'Sat',
      subjects: 'IELTS Academic',
      branchId: DEMO_HQ,
      teacherId: teachers[2]?.id || '',
      teacherName: teachers[2]?.fullName || '',
      students: String(students.filter((s) => s.batch === DEMO_BATCH_B).length),
      avg: '6.3',
    },
  ];

  localStorage.setItem('ielts-branches-v1', JSON.stringify(branches));
  localStorage.setItem('ielts-entities-teacher', JSON.stringify(teachers));
  localStorage.setItem('ielts-entities-student', JSON.stringify(students));
  localStorage.setItem('ielts-entities-batch', JSON.stringify(batches));
  localStorage.setItem('ielts-super-admin', JSON.stringify({
    fullName: 'Demo Super Admin',
    name: 'Demo Super Admin',
    username: 'super.admin',
    password: 'DemoSuper1!',
  }));
  // Drop stale v1 seed marker if present.
  localStorage.removeItem('ielts-demo-seeded-v1');
  localStorage.setItem(DEMO_SEEDED_KEY, '1');
}

export function enterDemoRole(role: 'admin' | 'branch-admin' | 'teacher' | 'student') {
  seedDemoWorld();
  if (role === 'admin') {
    ensureDemoSession('super_admin', { username: 'super.admin', full_name: 'Demo Super Admin', email: 'super.admin@demo.fti.local' });
    sessionStorage.setItem('ielts-role', 'admin');
    return '/admin/dashboard';
  }
  if (role === 'branch-admin') {
    ensureDemoSession('branch_admin', {
      username: 'nadia.admin',
      full_name: 'Nadia Admin',
      email: 'nadia.admin@demo.fti.local',
      branch_id: 'demo-gujranwala',
    });
    sessionStorage.setItem('ielts-branch-admin-id', 'demo-gujranwala');
    sessionStorage.setItem('ielts-branch-admin-username', 'nadia.admin');
    sessionStorage.setItem('ielts-role', 'branch-admin');
    return '/branch-admin/dashboard';
  }
  if (role === 'teacher') {
    const teachers = JSON.parse(localStorage.getItem('ielts-entities-teacher') || '[]') as {
      id: string; fullName: string; username: string; email: string; branchId: string; batches?: string;
    }[];
    const t = teachers.find((row) => (row.batches || '').includes(DEMO_BATCH_A)) || teachers[0];
    ensureDemoSession('teacher', {
      id: t?.id || 'demo-teacher-1',
      username: t?.username || 'teacher.demo',
      full_name: t?.fullName || 'Demo Teacher',
      email: t?.email || 'teacher@demo.fti.local',
      branch_id: t?.branchId || DEMO_HQ,
      batches: t?.batches || DEMO_BATCH_A,
    });
    if (t?.id) sessionStorage.setItem('ielts-current-teacher-id', t.id);
    sessionStorage.setItem('ielts-role', 'teacher');
    return '/teacher/dashboard';
  }
  const students = JSON.parse(localStorage.getItem('ielts-entities-student') || '[]') as {
    id: string; fullName: string; username: string; email: string; batch: string; branchId: string;
  }[];
  // Prefer Morning Batch A so teacher assign-to-batch lands in this inbox.
  const s =
    students.find((row) => row.batch === DEMO_BATCH_A && row.branchId === DEMO_HQ) ||
    students.find((row) => row.batch === DEMO_BATCH_A) ||
    students[0];
  ensureDemoSession('student', {
    id: s?.id || 'demo-student-1',
    username: s?.username || 'student.demo',
    full_name: s?.fullName || 'Demo Student',
    email: s?.email || 'student@demo.fti.local',
    branch_id: s?.branchId || DEMO_HQ,
    batch: s?.batch || DEMO_BATCH_A,
  });
  if (s) {
    sessionStorage.setItem(
      'ielts-current-student',
      JSON.stringify({
        id: s.id,
        name: s.fullName,
        batch: s.batch,
        email: s.email,
        username: s.username,
      }),
    );
    sessionStorage.setItem('writing_student_id', s.id);
  }
  sessionStorage.setItem('ielts-role', 'student');
  return '/student/dashboard';
}
