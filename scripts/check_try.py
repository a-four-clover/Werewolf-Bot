from pathlib import Path
p=Path('src')/ 'discord_bot.py'
if not p.exists():
    print('file not found:', p)
    raise SystemExit(1)
s=p.read_text(encoding='utf-8')
lines=s.splitlines()
for i,l in enumerate(lines, start=1):
    if 'try:' in l:
        indent=len(l)-len(l.lstrip())
        found=False
        for j in range(i, min(i+400, len(lines))):
            lj=lines[j]
            if lj.strip().startswith(('except','finally')):
                indentj=len(lj)-len(lj.lstrip())
                if indentj==indent:
                    found=True
                    break
        if not found:
            print('Unmatched try at', i, l.strip())
# also print sample context around known error line (1804-1812)
ln=1800
print('\nContext around line 1800:')
for k in range(ln, ln+40):
    if 1 <= k <= len(lines):
        print(f"{k:4}: {lines[k-1]}")
