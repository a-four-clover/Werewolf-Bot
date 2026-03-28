from pathlib import Path
p=Path(r'C:\Users\ytmk1\Desktop\hobbies\recorder\werewolf\src\discord_bot.py')
s=p.read_text(encoding='utf-8')
lines=s.splitlines()
stack=[]
for i,l in enumerate(lines, start=1):
    stripped=l.strip()
    if stripped.startswith('try:'):
        stack.append(('try',i))
    if stripped.startswith('except') or stripped.startswith('except '):
        if stack:
            for j in range(len(stack)-1,-1,-1):
                if stack[j][0]=='try':
                    stack.pop(j)
                    break
    if stripped.startswith('finally:'):
        if stack:
            for j in range(len(stack)-1,-1,-1):
                if stack[j][0]=='try':
                    stack.pop(j)
                    break
print('Unclosed try count:', len([t for t in stack if t[0]=='try']))
print('Sample remaining (up to 40):')
for t in stack[:40]:
    print(t)
