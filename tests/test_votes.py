"""python -m tests.test_votes"""
from hollowfloor.votes import decide, parse_vote, tally

P = lambda t: parse_vote(t, "OPEN", "RUN")
assert P("OPEN IT!!") == "A" and P("run. just run") == "B" and P("1") == "A" and P("option 2") == "B" and P("b") == "B"
assert P("open the door and run") is None          # names both: ignored
assert P("this is so scary") is None               # names neither
assert P("I'd reopen it") is None                  # whole words only
assert P("12") is None and P("a lot of fun") is None  # numbers/letters only count on their own

yt = [{"author": "u1", "text": "open", "at": "1"}, {"author": "u1", "text": "actually run", "at": "2"},  # changed mind: latest counts
      {"author": "u2", "text": "OPEN", "at": "1"}, {"author": "u3", "text": "open!", "at": "1"},
      {"author": "chan", "text": "1", "at": "1"}]                                                          # our own comment: ignored
ig = [{"author": "u1", "text": "open", "at": "1"}]  # same name on another platform counts separately
c = tally({"youtube": yt, "instagram": ig}, "OPEN", "RUN", own_ids={"chan"})
assert (c["A"], c["B"]) == (3, 1), c
assert c["by_platform"]["youtube"] == {"A": 2, "B": 1}

assert decide({"A": 3, "B": 1}, "B", 5)[0] == "B"   # too few votes -> default
assert decide({"A": 4, "B": 4}, "B", 5)[0] == "B"   # tie -> default
assert decide({"A": 2, "B": 7}, "A", 5)[0] == "B"   # majority wins
print("votes self-check: OK")
