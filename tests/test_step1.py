"""Step 1 self-check: run with `python -m tests.test_step1`."""
import logging
import os

from hollowfloor import config, db


def main():
    con = db.connect(":memory:")
    sid = con.execute("INSERT INTO stories (premise, story_type) VALUES ('The elevator stops at a floor that is not on the panel', 'empty buildings')").lastrowid
    assert db.premise_exists(con, "  the elevator stops at a floor that is not on the panel ")

    for s in db.STORY_FLOW[1:]:
        db.set_story_status(con, sid, s)
    for bad in ("posted", "idea", "rejected"):  # repeat, backwards, reject after posting
        try:
            db.set_story_status(con, sid, bad)
            raise AssertionError(f"allowed {bad}")
        except ValueError:
            pass

    sid2 = con.execute("INSERT INTO stories (premise) VALUES ('x')").lastrowid
    try:
        db.set_story_status(con, sid2, "approved")  # skip
        raise AssertionError("allowed skip")
    except ValueError:
        pass
    db.set_story_status(con, sid2, "rejected")

    r1 = db.start_run(con); db.finish_run(con, r1, False)
    assert not db.two_failed_runs_in_a_row(con)
    r2 = db.start_run(con); db.finish_run(con, r2, False)
    assert db.two_failed_runs_in_a_row(con)

    # secret redaction
    os.environ["DISCORD_BOT_TOKEN"] = "super-secret-token-123"
    records = []
    log = config.get_logger("t")
    log.addHandler(type("H", (logging.Handler,), {"emit": lambda self, r: records.append(r.getMessage())})())
    log.handlers[-1].addFilter(config._Redact())
    log.info("token is %s", "super-secret-token-123")
    assert "super-secret" not in records[-1], records[-1]

    assert config.CONFIG["channel"]["name"] == "Hollow Floor"
    print("step 1 self-check: OK")


if __name__ == "__main__":
    main()
