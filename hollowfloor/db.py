"""SQLite store. The scheduler only picks up work by status, so any run can resume after a crash."""
import sqlite3
from datetime import datetime, timezone

from .config import CONFIG, ROOT

STORY_FLOW = ["idea", "scripted", "approved", "rendered", "scheduled", "posted", "analyzed"]
# 'rejected' is terminal and reachable from any pre-posted state (failed quality gate twice, or owner said no).

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
  id INTEGER PRIMARY KEY,
  premise TEXT NOT NULL UNIQUE,
  story_type TEXT,
  genre TEXT,
  title TEXT, caption TEXT, hashtags TEXT, thumbnail_text TEXT,
  idea TEXT, outline TEXT,
  -- viewer vote: Part 1 ends on a choice, Part 2 follows the majority (both branches are written up front)
  choice_a TEXT, choice_b TEXT,
  default_choice TEXT CHECK (default_choice IN ('A', 'B')),  -- used on a tie or too few votes
  winning_choice TEXT CHECK (winning_choice IN ('A', 'B')),
  vote_counts TEXT,             -- JSON {"A": n, "B": n, "by_platform": {...}}
  status TEXT NOT NULL DEFAULT 'idea',
  quality_scores TEXT,          -- JSON
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scripts (
  id INTEGER PRIMARY KEY,
  story_id INTEGER NOT NULL REFERENCES stories(id),
  part INTEGER NOT NULL CHECK (part IN (1, 2)),
  branch TEXT NOT NULL DEFAULT '' CHECK (branch IN ('', 'A', 'B')),  -- '' for Part 1; Part 2 has one script per choice
  text TEXT NOT NULL,
  word_count INTEGER,
  voice_settings TEXT,          -- JSON
  UNIQUE (story_id, part, branch)
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY,
  story_id INTEGER NOT NULL REFERENCES stories(id),
  part INTEGER NOT NULL CHECK (part IN (1, 2)),
  branch TEXT NOT NULL DEFAULT '',  -- which Part 2 branch was rendered (only the winner is)
  file_path TEXT, thumbnail_path TEXT,
  seconds REAL,
  rendered_at TEXT,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending, rendered, review, approved, declined, posted, deleted
  review_note TEXT,
  UNIQUE (story_id, part)
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY,
  video_id INTEGER NOT NULL REFERENCES videos(id),
  platform TEXT NOT NULL,
  variant TEXT,                 -- A/B title+thumbnail version
  title TEXT, caption TEXT,
  scheduled_at TEXT, posted_at TEXT,
  url TEXT, platform_post_id TEXT,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued, blocked, posted, failed
  attempts INTEGER NOT NULL DEFAULT 0,
  UNIQUE (video_id, platform)
);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY,
  post_id INTEGER NOT NULL REFERENCES posts(id),
  views INTEGER, watch_time_s REAL, likes INTEGER, follows INTEGER, saves INTEGER,
  checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY,
  story_id INTEGER REFERENCES stories(id),
  tested TEXT NOT NULL, version_a TEXT, version_b TEXT, winner TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  step TEXT NOT NULL, message TEXT, fixed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (                 -- small settings/state: first post date, pauses, last summary...
  key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS runs (               -- one row per scheduled run, for health checks
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT, ok INTEGER, summary TEXT
);
"""


# columns added after the first release: (table, column, type). Applied once to older databases.
MIGRATIONS = [("videos", "review_msg_id", "TEXT"), ("videos", "reminded", "INTEGER DEFAULT 0"), ("videos", "review_sent_at", "TEXT"),
              ("posts", "error", "TEXT"), ("metrics", "kind", "TEXT"), ("metrics", "raw", "TEXT"), ("videos", "artifact", "TEXT")]


def connect(path=None):
    import os
    con = sqlite3.connect(path or os.environ.get("HF_DB") or ROOT / CONFIG["paths"]["db"])
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        if col not in [r[1] for r in con.execute(f"PRAGMA table_info({table})")]:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    con.commit()
    return con


def kv_get(con, key, default=None):
    r = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def kv_set(con, key, value):
    con.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    con.commit()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def set_story_status(con, story_id, new):
    """Moves a story forward exactly one step (or to 'rejected'). Refuses skips and repeats."""
    cur = con.execute("SELECT status FROM stories WHERE id=?", (story_id,)).fetchone()["status"]
    ok = (new == "rejected" and cur not in ("posted", "analyzed", "rejected")) or \
         (cur in STORY_FLOW and STORY_FLOW.index(new) == STORY_FLOW.index(cur) + 1 if new in STORY_FLOW else False)
    if not ok:
        raise ValueError(f"story {story_id}: illegal transition {cur} -> {new}")
    con.execute("UPDATE stories SET status=? WHERE id=?", (new, story_id))
    con.commit()


def stories_with_status(con, status, limit=100):
    return con.execute("SELECT * FROM stories WHERE status=? ORDER BY id LIMIT ?", (status, limit)).fetchall()


def premise_exists(con, premise):
    return con.execute("SELECT 1 FROM stories WHERE lower(premise)=lower(?)", (premise.strip(),)).fetchone() is not None


def log_error(con, step, message):
    con.execute("INSERT INTO errors (step, message) VALUES (?, ?)", (step, str(message)[:2000]))
    con.commit()


def start_run(con):
    rid = con.execute("INSERT INTO runs (started_at) VALUES (?)", (now(),)).lastrowid
    con.commit()
    return rid


def finish_run(con, run_id, ok, summary=""):
    con.execute("UPDATE runs SET finished_at=?, ok=?, summary=? WHERE id=?", (now(), int(ok), summary, run_id))
    con.commit()


def two_failed_runs_in_a_row(con):
    last = con.execute("SELECT ok FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 2").fetchall()
    return len(last) == 2 and all(r["ok"] == 0 for r in last)
