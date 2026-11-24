from datetime import datetime, timezone
from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import select

from core.audit import record
from core.db import transaction
from core.roles import Role
from domain.catalog import one, query
from domain.interventions import findings
from domain.review_tables import (review_arbitrations, review_batches,
                                  review_listens, review_versions, review_windows)
from domain.soundboard import windows
from domain.states import Howl, ReviewSeat

# 两名现场听音复核员的座席；交齐前双方互不可见
SEAT_BY_ROLE = {Role.REVIEWER.value: ReviewSeat.A, Role.REVIEWER2.value: ReviewSeat.B}
# 机器把局部回授与同频协作都视作啸叫告警，正常即未告警
HOWL_CATEGORIES = ("local", "coordinated")
SEAL_ROLES = (Role.REVIEWER.value, Role.REVIEWER2.value, Role.ARBITER.value)


def now():
    return datetime.now(timezone.utc)


def get_batch(conn, code, lock=False):
    query_batch = select(review_batches).where(review_batches.c.code == code)
    row = conn.execute(query_batch.with_for_update() if lock else query_batch).mappings().first()
    if row is None:
        raise HTTPException(404, f"没有编号为 {code} 的复核批次")
    return dict(row)


def lock_batch(conn, code):
    return get_batch(conn, code, lock=True)


def batch_window_rows(conn, batch_code):
    stmt = (select(review_windows, windows.c.point, windows.c.slot, windows.c.frequency,
                   findings.c.category, findings.c.score)
            .join(windows, windows.c.code == review_windows.c.window)
            .join(findings, findings.c.code == review_windows.c.finding)
            .where(review_windows.c.batch == batch_code)
            .order_by(review_windows.c.id))
    return [dict(row) for row in conn.execute(stmt).mappings()]


def list_rows(conn, table, *filters):
    stmt = select(table).where(*filters).order_by(table.c.id)
    return [dict(row) for row in conn.execute(stmt).mappings()]


def listens_map(conn, batch_code):
    result = {}
    for row in list_rows(conn, review_listens, review_listens.c.batch == batch_code):
        result.setdefault(row["window"], {})[row["seat"]] = row
    return result


def arbitrations_map(conn, batch_code):
    return {row["window"]: row
            for row in list_rows(conn, review_arbitrations, review_arbitrations.c.batch == batch_code)}


def create_batch(form, user):
    codes = list(dict.fromkeys(form.findings))
    if len(codes) != len(form.findings):
        raise HTTPException(422, "同一批次内的发现不可重复")
    with transaction() as conn:
        lock = conn.execute(select(review_batches).where(review_batches.c.code == form.code)
                            .with_for_update()).mappings().first()
        if lock is not None:
            raise HTTPException(409, "复核批次编号已存在")
        conn.execute(review_batches.insert().values(code=form.code, created_by=user["sub"], created_at=now()))
        for finding_code in codes:
            note = one(conn, findings, finding_code)
            window_code = note["focus_window"]
            machine_howl = 1 if note["category"] in HOWL_CATEGORIES else 0
            conn.execute(review_windows.insert().values(
                batch=form.code, finding=finding_code, window=window_code, machine_howl=machine_howl))
        record(conn, user, f"review-batch:{form.code}:{len(codes)}")
        return one(conn, review_batches, form.code)


def submit_listen(form, user):
    seat = SEAT_BY_ROLE.get(user["role"])
    if seat is None:
        raise HTTPException(403, "仅两名现场听音复核员可交听音记录")
    with transaction() as conn:
        lock_batch(conn, form.batch)
        target = conn.execute(select(review_windows).where(
            review_windows.c.batch == form.batch, review_windows.c.window == form.window)
            .with_for_update()).mappings().first()
        if target is None:
            raise HTTPException(404, "该声检窗不在本复核批次中")
        rows = batch_window_rows(conn, form.batch)
        if all(len(listens_map(conn, form.batch).get(r["window"], {})) == 2 for r in rows):
            raise HTTPException(409, "双方记录已交齐，现场听音记录锁定")
        existing = conn.execute(select(review_listens).where(
            review_listens.c.batch == form.batch, review_listens.c.window == form.window,
            review_listens.c.seat == seat.value).with_for_update()).mappings().first()
        if existing is None:
            conn.execute(review_listens.insert().values(
                batch=form.batch, window=form.window, seat=seat.value, verdict=form.verdict,
                note=form.note, reviewer=user["sub"], updated_at=now()))
        else:
            conn.execute(review_listens.update().where(review_listens.c.id == existing["id"]).values(
                verdict=form.verdict, note=form.note, reviewer=user["sub"], updated_at=now()))
        record(conn, user, f"review-listen:{form.batch}:{form.window}:{seat.value}:{form.verdict}")
        return {"batch": form.batch, "window": form.window, "seat": seat.value, "verdict": form.verdict}


def decide_arbitration(form, user):
    with transaction() as conn:
        lock_batch(conn, form.batch)
        target = conn.execute(select(review_windows).where(
            review_windows.c.batch == form.batch, review_windows.c.window == form.window)
            .with_for_update()).mappings().first()
        if target is None:
            raise HTTPException(404, "该声检窗不在本复核批次中")
        listens = listens_map(conn, form.batch).get(form.window, {})
        if set(listens) != {ReviewSeat.A.value, ReviewSeat.B.value}:
            raise HTTPException(409, "须等两名复核员交齐记录后才能裁定")
        if listens[ReviewSeat.A.value]["verdict"] == listens[ReviewSeat.B.value]["verdict"]:
            raise HTTPException(409, "双方意见一致，无需第三人裁定")
        if form.window in arbitrations_map(conn, form.batch):
            raise HTTPException(409, "该窗已有第三人裁定")
        conn.execute(review_arbitrations.insert().values(
            batch=form.batch, window=form.window, verdict=form.verdict, note=form.note,
            arbiter=user["sub"], decided_at=now()))
        record(conn, user, f"review-arbitration:{form.batch}:{form.window}:{form.verdict}")
        return {"batch": form.batch, "window": form.window, "verdict": form.verdict}


def window_outcome(row, listens, arbitration):
    seats = listens.get(row["window"], {})
    verdict_a = seats.get(ReviewSeat.A.value)
    verdict_b = seats.get(ReviewSeat.B.value)
    machine_howl = bool(row["machine_howl"])
    item = {"window": row["window"], "finding": row["finding"], "point": row["point"],
            "slot": row["slot"], "frequency": row["frequency"],
            "machine_category": row["category"], "machine_howl": machine_howl,
            "verdict_a": verdict_a["verdict"] if verdict_a else None,
            "verdict_b": verdict_b["verdict"] if verdict_b else None,
            "arbitration": arbitration["verdict"] if arbitration else None,
            "final_verdict": None, "included": False, "outcome": "pending", "basis": ""}
    if verdict_a is None or verdict_b is None:
        item["basis"] = "听音记录未交齐，不参与本版漏报率"
        return item
    if verdict_a["verdict"] == verdict_b["verdict"]:
        item["final_verdict"] = verdict_a["verdict"]
        item["basis"] = "两名复核员意见一致"
    elif arbitration is not None:
        item["final_verdict"] = arbitration["verdict"]
        item["basis"] = "双方意见相反，以第三人裁定为准"
    else:
        item["outcome"] = "unresolved"
        item["basis"] = "双方意见相反且尚无裁定，不计入漏报率"
        return item
    item["included"] = True
    if item["final_verdict"] == Howl.HOWL.value:
        item["outcome"] = "detected" if machine_howl else "missed"
    else:
        item["outcome"] = "false_alarm" if machine_howl else "clear_ok"
    return item


def snapshot(conn, batch_code):
    rows = batch_window_rows(conn, batch_code)
    listens = listens_map(conn, batch_code)
    arbitrations = arbitrations_map(conn, batch_code)
    items = [window_outcome(row, listens, arbitrations.get(row["window"])) for row in rows]
    decided = [x for x in items if x["included"]]
    true_howl = [x for x in decided if x["final_verdict"] == Howl.HOWL.value]
    missed = [x for x in true_howl if not x["machine_howl"]]
    metrics = {
        "total_windows": len(items),
        "decided_windows": len(decided),
        "excluded_windows": len(items) - len(decided),
        "unresolved_windows": sum(1 for x in items if x["outcome"] == "unresolved"),
        "pending_windows": sum(1 for x in items if x["outcome"] == "pending"),
        "true_howl_windows": len(true_howl),
        "missed_windows": len(missed),
        "false_alarm_windows": sum(1 for x in decided if x["outcome"] == "false_alarm"),
        "miss_rate": (len(missed) / len(true_howl)) if true_howl else None,
    }
    fingerprint_src = [{"window": x["window"], "machine_howl": x["machine_howl"],
                        "outcome": x["outcome"], "final_verdict": x["final_verdict"]}
                       for x in sorted(items, key=lambda x: x["window"])]
    fingerprint = sha256(json.dumps(fingerprint_src, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
    return items, metrics, fingerprint


def seal(form, user):
    if user["role"] not in SEAL_ROLES:
        raise HTTPException(403, "当前角色无权封存复核批次")
    with transaction() as conn:
        # 与裁定/听音写入争抢同一把批次锁，保证封存快照与逐窗依据取自同一时点
        lock_batch(conn, form.batch)
        items, metrics, fingerprint = snapshot(conn, form.batch)
        previous = conn.execute(select(review_versions).where(review_versions.c.batch == form.batch)
                                .order_by(review_versions.c.version.desc())).mappings().first()
        if previous is not None and previous["fingerprint"] == fingerprint:
            # 逐窗结论没有变化：封存重试不新增版本，直接回传已封存版本
            return {"retried": True, "version": dict(previous)}
        next_version = 1 if previous is None else previous["version"] + 1
        sealed_at = now()
        conn.execute(review_versions.insert().values(
            batch=form.batch, version=next_version, fingerprint=fingerprint, metrics=metrics,
            windows=items, sealed_by=user["sub"], sealed_at=sealed_at))
        record(conn, user, f"review-seal:{form.batch}:v{next_version}")
        saved = conn.execute(select(review_versions).where(
            review_versions.c.batch == form.batch,
            review_versions.c.version == next_version)).mappings().first()
        return {"retried": False, "version": dict(saved)}


def list_batches():
    with transaction() as conn:
        result = []
        for batch in query(conn, review_batches):
            rows = batch_window_rows(conn, batch["code"])
            listens = listens_map(conn, batch["code"])
            versions = list_rows(conn, review_versions, review_versions.c.batch == batch["code"])
            seats = {ReviewSeat.A.value: 0, ReviewSeat.B.value: 0}
            for by_window in listens.values():
                for seat in by_window:
                    seats[seat] += 1
            result.append({"code": batch["code"], "created_by": batch["created_by"],
                           "created_at": batch["created_at"], "total_windows": len(rows),
                           "submitted": seats,
                           "complete": len(rows) > 0 and all(
                               len(listens.get(r["window"], {})) == 2 for r in rows),
                           "version_count": len(versions)})
        return result


def detail(code, user):
    with transaction() as conn:
        get_batch(conn, code)
        rows = batch_window_rows(conn, code)
        listens = listens_map(conn, code)
        arbitrations = arbitrations_map(conn, code)
        complete = bool(rows) and all(len(listens.get(r["window"], {})) == 2 for r in rows)
        own_seat = SEAT_BY_ROLE.get(user["role"])
        window_views = []
        for row in rows:
            seats = listens.get(row["window"], {})
            view = {"window": row["window"], "finding": row["finding"], "point": row["point"],
                    "slot": row["slot"], "frequency": row["frequency"],
                    "machine_category": None, "machine_howl": None,
                    "listens": {ReviewSeat.A.value: None, ReviewSeat.B.value: None},
                    "arbitration": None, "complete": len(seats) == 2,
                    "agreement": None}
            if complete:
                view["machine_category"] = row["category"]
                view["machine_howl"] = bool(row["machine_howl"])
                view["agreement"] = (seats.get(ReviewSeat.A.value) and seats.get(ReviewSeat.B.value)
                                      and seats[ReviewSeat.A.value]["verdict"]
                                      == seats[ReviewSeat.B.value]["verdict"])
                for seat, item in seats.items():
                    view["listens"][seat] = {"verdict": item["verdict"], "note": item["note"],
                                             "reviewer": item["reviewer"]}
                if row["window"] in arbitrations:
                    ruling = arbitrations[row["window"]]
                    view["arbitration"] = {"verdict": ruling["verdict"], "note": ruling["note"],
                                           "arbiter": ruling["arbiter"]}
            elif own_seat is not None and own_seat.value in seats:
                mine = seats[own_seat.value]
                view["listens"][own_seat.value] = {"verdict": mine["verdict"], "note": mine["note"],
                                                    "reviewer": mine["reviewer"]}
            window_views.append(view)
        versions = [dict(v) for v in list_rows(conn, review_versions, review_versions.c.batch == code)]
        return {"code": code, "complete": complete,
                "submitted": {seat: sum(1 for w in listens.values() if seat in w)
                              for seat in (ReviewSeat.A.value, ReviewSeat.B.value)},
                "total_windows": len(rows), "windows": window_views, "versions": versions}
