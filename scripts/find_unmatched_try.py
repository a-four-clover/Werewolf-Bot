from pathlib import Path
import re
p=Path('src')/'discord_bot.py'
s=p.read_text(encoding='utf-8')
lines=s.splitlines()
try_positions=[]
for i,l in enumerate(lines, start=1):
    stripped=l.lstrip()
    if stripped.startswith('#'):
        continue
    # ignore strings by quick heuristic: skip lines with triple quotes start/end (not perfect)
    if re.match(r"\s*([ruRUfF]?\"\"\"|[ruRUfF]?''' )", l):
        # naive: skip this line
        continue
    if re.search(r"\btry:\s*$", l):
        indent=len(l)-len(l.lstrip())
        try_positions.append((i, indent))

unmatched=[]
for pos, indent in try_positions:
    found=False
    for j in range(pos+1, min(pos+500, len(lines))):
        lj=lines[j]
        if lj.lstrip().startswith('#'):
            continue
        stripped=lj.strip()
        if re.match(r'(except\b|finally\b)', stripped):
            indentj=len(lj)-len(lj.lstrip())
            if indentj==indent:
                found=True
                break
    if not found:
        unmatched.append((pos, indent, lines[pos-1].strip()))

print('Unmatched try count:', len(unmatched))
for u in unmatched:
    print('Unmatched at', u[0], 'indent', u[1], 'line:', u[2])
    start=max(1,u[0]-3)
    for k in range(start, u[0]+8):
        if 1<=k<=len(lines):
            print(f"{k:4}: {lines[k-1]}")
    print('---')
