from pathlib import Path
p=Path(r'C:\Users\ytmk1\Desktop\hobbies\recorder\werewolf\src\discord_bot.py')
lines=p.read_text(encoding='utf-8').splitlines()
stack=[]
for i,l in enumerate(lines, start=1):
    stripped=l.lstrip('\t')
    indent=len(l)-len(l.lstrip(' '))
    s=l.strip()
    if s.startswith('try:'):
        stack.append((i, indent, 'try'))
    if s.startswith('except') or s.startswith('finally'):
        # find most recent try at same indent
        found=False
        for j in range(len(stack)-1,-1,-1):
            if stack[j][2]=='try' and stack[j][1]==indent:
                stack.pop(j)
                found=True
                break
        if not found:
            print('orphan except at',i,'indent',indent)
    if s.startswith('def ') or s.startswith('async def ') or s.startswith('class '):
        # on new def/class, report any try at same indent that hasn't seen an except/finally
        to_report=[t for t in stack if t[1]==indent]
        if to_report:
            print('Before def at line',i,'there are unclosed try at same indent:')
            for t in to_report:
                print('  try at',t)
print('Done. remaining stack length:',len(stack))
for t in stack[:20]:
    print('rem:',t)
