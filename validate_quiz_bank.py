import re
from quiz_bank import QUIZ_BANK

assert len(QUIZ_BANK) == 1000, f"Expected 1000 questions, got {len(QUIZ_BANK)}"
seen = set()
for i, row in enumerate(QUIZ_BANK, 1):
    assert len(row) == 9, f"Row {i}: expected 9 fields"
    q, a, b, c, d, correct, explanation, difficulty, category = row
    key = re.sub(r"\s+", " ", q.casefold()).strip()
    assert key not in seen, f"Duplicate question: {q}"
    seen.add(key)
    assert all(str(x).strip() for x in (q, a, b, c, d)), f"Empty field at {i}"
    assert len({x.casefold().strip() for x in (a,b,c,d)}) == 4, f"Duplicate options at {i}"
    assert correct in "ABCD", f"Bad correct option at {i}"
    assert difficulty == "very_hard", f"Bad difficulty at {i}"

cats = {}
for row in QUIZ_BANK:
    cats[row[-1]] = cats.get(row[-1], 0) + 1
print("Quiz bank OK")
print("Total:", len(QUIZ_BANK))
print("Categories:", cats)
