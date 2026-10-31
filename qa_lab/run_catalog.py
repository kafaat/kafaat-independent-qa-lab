"""Bounded, parameterized SQLite run listing for the local evidence API.

A catalog entry is *not* a receipt or a source attestation.
"""
from contextlib import closing
from urllib.parse import parse_qs

STATUSES = frozenset({"PENDING", "RUNNING", "PASSED", "FAILED", "BLOCKED", "TIMED_OUT"})


def filters(query):
    if len(query) > 2048:
        raise ValueError("query too long")
    values = parse_qs(query, keep_blank_values=True, max_num_fields=6)
    if set(values) - {"status", "project", "q", "limit", "offset"}:
        raise ValueError("unknown filter")
    if any(len(value) != 1 for value in values.values()):
        raise ValueError("repeated filter")
    value = {key: item[0] for key, item in values.items()}
    status = value.get("status", "")
    project = value.get("project", "")
    search = value.get("q", "")
    if status and status not in STATUSES:
        raise ValueError("invalid status")
    if any(len(v) > 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in v)
           for v in (project, search)):
        raise ValueError("invalid search")
    limit = value.get("limit", "25")
    offset = value.get("offset", "0")
    if (not limit.isascii() or not limit.isdecimal()
            or not 1 <= int(limit) <= 100):
        raise ValueError("invalid limit")
    if (not offset.isascii() or not offset.isdecimal()
            or not 0 <= int(offset) <= 10000):
        raise ValueError("invalid offset")
    return {"status": status, "project": project, "q": search,
            "limit": int(limit), "offset": int(offset)}


def list_runs(lab, options):
    conditions = []
    args = []
    if options["status"]:
        conditions.append("status = ?")
        args.append(options["status"])
    if options["project"]:
        conditions.append("project = ?")
        args.append(options["project"])
    if options["q"]:
        conditions.append("(instr(lower(project), lower(?)) > 0 OR instr(lower(id), lower(?)) > 0)")
        args.extend([options["q"], options["q"]])
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = ("SELECT id,project,source_sha,recipe,status FROM runs" + where
           + " ORDER BY created DESC,id DESC LIMIT ? OFFSET ?")
    with closing(lab.connect()) as db:
        rows = db.execute(sql, (*args, options["limit"] + 1, options["offset"])).fetchall()
    size = options["limit"]
    return {"runs": [{"run_id": r[0], "project": r[1], "source_sha": r[2],
                      "recipe": r[3], "status": r[4]} for r in rows[:size]],
            "next_offset": options["offset"] + size if len(rows) > size else None,
            "ordering": "created-desc-id-desc",
            "authority": "execution-evidence-only"}
