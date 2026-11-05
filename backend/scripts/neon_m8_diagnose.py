"""Neon batch/student label integrity diagnostic (read-only)."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func

from app.auth.models import Batch, Branch, User
from app.database import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        print("=== batches ===")
        for b in db.query(Batch).all():
            tid = (b.teacher_id or "")[:8]
            print(f"  id={b.id[:8]} name={b.name!r} branch={b.branch_id!r} teacher={tid}")

        dups = (
            db.query(Batch.branch_id, Batch.name, func.count())
            .group_by(Batch.branch_id, Batch.name)
            .having(func.count() > 1)
            .all()
        )
        print("=== duplicate (branch_id,name) ===", dups or "none")

        by_branch: dict[str, list[str]] = defaultdict(list)
        for b in db.query(Batch).all():
            by_branch[b.branch_id or ""].append(b.name)
        print("=== case-normalized dups per branch ===")
        for bid, names in by_branch.items():
            low = [n.lower().strip() for n in names]
            if len(low) != len(set(low)):
                print("  ", bid, names)

        batches = {(b.branch_id, b.name) for b in db.query(Batch).all()}
        batches_lower = {(b.branch_id, (b.name or "").lower().strip()) for b in db.query(Batch).all()}
        name_to_branches: dict[str, set[str]] = defaultdict(set)
        for b in db.query(Batch).all():
            name_to_branches[b.name].add(b.branch_id or "")
        branch_ids = {br.id for br in db.query(Branch).all()}

        A = B = C = E = F = 0
        samples: list[tuple] = []
        for s in db.query(User).filter_by(role="student").all():
            label = (s.batch or "").strip()
            bid = (s.branch_id or "").strip()
            br = db.query(Branch).filter_by(id=bid).first() if bid else None
            if not label:
                E += 1
                continue
            if not bid or bid not in branch_ids:
                A += 1
                samples.append(("A-orphan-branch", s.username, label))
                continue
            inactive = bool(br and not br.is_active)
            if (bid, label) in batches:
                if inactive:
                    F += 1
                    samples.append(("F-inactive-branch-ok-batch", s.username, label))
                continue
            other = name_to_branches.get(label, set()) - {bid}
            if other:
                B += 1
                samples.append(("B-other-branch", s.username, label, bid))
            elif (bid, label.lower().strip()) in batches_lower:
                C += 1
                samples.append(("C-case", s.username, label))
            else:
                A += 1
                samples.append(("A-missing", s.username, label, bid))
            if inactive:
                F += 1

        print("=== mismatch classification ===")
        print(f"  A nonexistent/orphan label: {A}")
        print(f"  B label exists other branch: {B}")
        print(f"  C case-only mismatch: {C}")
        print(f"  E empty/unassigned: {E}")
        print(f"  F inactive-branch students (any): {F}")
        print("  samples:", samples[:15])
        print(f"  students={db.query(User).filter_by(role='student').count()} batches={db.query(Batch).count()}")

        # teacher CSV vs Batch.teacher_id drift
        drift = 0
        for t in db.query(User).filter_by(role="teacher").all():
            names = {p.strip() for p in (t.batches or "").split(",") if p.strip() and p.strip() != "—"}
            owned = {b.name for b in db.query(Batch).filter_by(teacher_id=t.id).all()}
            if names != owned:
                drift += 1
                print(f"  drift teacher={t.username} csv={names} teacher_id_batches={owned}")
        print(f"=== teacher sync drift count: {drift} ===")
    finally:
        db.close()


if __name__ == "__main__":
    main()
