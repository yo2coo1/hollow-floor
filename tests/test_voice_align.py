"""python -m tests.test_voice_align"""
from hollowfloor.voice import align

h = [{'w': 'never', 't0': 1, 't1': 1.3}, {'w': 'opened', 't0': 1.3, 't1': 1.6}, {'w': 'light', 't0': 2, 't1': 2.3}, {'w': 'blinking', 't0': 2.3, 't1': 2.8}]
r = align('never open the lights blinking...'.split(), h)
assert [x['w'] for x in r] == ['never', 'open', 'the', 'lights', 'blinking...']  # script words, not misheard ones
assert all(a['t0'] <= b['t0'] for a, b in zip(r, r[1:])) and r[0]['t0'] == 1 and r[-1]['t1'] == 2.8
r = align('one two three'.split(), [{'w': 'one', 't0': 0, 't1': 0.2}])  # trailing words Whisper missed
assert r[2]['t0'] > r[1]['t0'] > 0
print('voice align self-check: OK')
