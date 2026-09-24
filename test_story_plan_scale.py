"""Story Plan list regression: payload size may grow, SQL statement count must not."""
from __future__ import annotations

from contextlib import contextmanager
import os
import shutil
import time
import uuid


ROOT = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(ROOT, ".story_plan_scale_tmp", uuid.uuid4().hex[:10])
os.makedirs(TMP, exist_ok=True)
os.environ["DB_PATH"] = os.path.join(TMP, "test.db")

import db  # noqa: E402


def insert_nodes(work_id: int, start: int, stop: int) -> None:
    now = time.time()
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT INTO story_plan_nodes(work_id,node_type,title,summary,status,context_policy,ord,revision,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(work_id, "chapter", f"第{index}章计划", f"摘要 {index}", "planned", "auto",
              index, 1, now, now) for index in range(start, stop)],
        )


def measured_list(work_id: int, user_id: int):
    original_get_conn = db.get_conn
    statements = []

    @contextmanager
    def traced_conn():
        with original_get_conn() as conn:
            conn.set_trace_callback(statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)

    db.get_conn = traced_conn
    try:
        started = time.perf_counter()
        rows = db.list_story_plan_nodes(work_id, user_id)
        elapsed = time.perf_counter() - started
    finally:
        db.get_conn = original_get_conn
    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    return rows, selects, elapsed


try:
    db.init_db()
    user = db.create_user("scale-user", "scale-pass")
    work = db.create_work(user["id"], "规模测试")

    insert_nodes(work["id"], 1, 301)
    rows_300, selects_300, elapsed_300 = measured_list(work["id"], user["id"])
    assert len(rows_300) == 300

    insert_nodes(work["id"], 301, 1001)
    rows_1000, selects_1000, elapsed_1000 = measured_list(work["id"], user["id"])
    assert len(rows_1000) == 1000
    assert len(selects_300) == len(selects_1000) == 4, (len(selects_300), len(selects_1000))
    print(
        "Story Plan scale check passed: "
        f"300 rows {elapsed_300:.3f}s, 1000 rows {elapsed_1000:.3f}s, "
        f"{len(selects_1000)} SELECT statements."
    )
finally:
    shutil.rmtree(TMP, ignore_errors=True)
