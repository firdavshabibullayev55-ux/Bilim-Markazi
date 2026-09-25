import importlib.util
import os
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / '.env'

required = {
    'aiogram': 'Telegram bot framework',
    'dotenv': 'python-dotenv',
    'aiohttp': 'HTTP client',
    'ddgs': 'Internet search',
    'pypdf': 'PDF text extraction',
    'openai': 'AI/OpenRouter client',
}

print('=== Bilim Markazi — tekshiruv ===')
failed = False

# Katta Quiz static bank validation
BANK = ROOT / 'quiz_bank.py'
if BANK.exists():
    try:
        tree = ast.parse(BANK.read_text(encoding='utf-8'))
        node = next(
            n for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'QUIZ_BANK' for t in n.targets)
        )
        bank = ast.literal_eval(node.value)
        questions = [str(x[0]).strip().casefold() for x in bank]
        bank_ok = (
            len(bank) == 1000
            and len(set(questions)) == 1000
            and all(isinstance(x, (tuple, list)) and len(x) == 9 for x in bank)
            and all(str(x[5]).upper() in {'A','B','C','D'} for x in bank)
        )
        print(f"quiz_bank.py: {'OK — 1000 ta takrorlanmaydigan savol' if bank_ok else 'X — baza tekshiruvidan o‘tmadi'}")
        failed |= not bank_ok
    except Exception as exc:
        print(f"quiz_bank.py: X — {exc}")
        failed = True
else:
    print('quiz_bank.py: X — fayl topilmadi')
    failed = True
for module, label in required.items():
    ok = importlib.util.find_spec(module) is not None
    print(('OK  ' if ok else 'X   ') + f'{module}: {label}')
    failed |= not ok

print(f'\n.env: {"topildi" if ENV.exists() else "TOPILMADI"}')
if ENV.exists():
    values = {}
    for raw in ENV.read_text(encoding='utf-8', errors='ignore').splitlines():
        raw = raw.strip()
        if not raw or raw.startswith('#') or '=' not in raw:
            continue
        k, v = raw.split('=', 1)
        values[k.strip()] = v.strip()
    for key in ('BOT_TOKEN', 'ADMIN_IDS', 'CHANNEL_ID', 'CHANNEL_URL'):
        print(f'{key}: {"OK" if values.get(key) else "BO‘SH"}')
    failed |= not values.get('BOT_TOKEN') or not values.get('CHANNEL_ID')

print('\n' + ('HAMMASI TAYYOR.' if not failed else 'YUQORIDAGI XATOLARNI TUZATING.'))
raise SystemExit(0 if not failed else 1)
