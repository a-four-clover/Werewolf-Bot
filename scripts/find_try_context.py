from pathlib import Path
p=Path(r'C:\Users\ytmk1\Desktop\hobbies\recorder\werewolf\src\discord_bot.py')
s=p.read_text(encoding='utf-8')
lines=s.splitlines()
loc=3455
# find last 'try:' before loc
last_try=None
for i in range(loc-1,0,-1):
    if lines[i-1].strip().startswith('try:'):
        last_try=(i,lines[i-1])
        break
print('last_try before',loc,':',last_try)
# find if there is an except between that try and loc
if last_try:
    ti=last_try[0]
    for j in range(ti+1,loc):
        if lines[j-1].strip().startswith('except') or lines[j-1].strip().startswith('finally:'):
            print('found except/finally at',j,lines[j-1].strip())
            break
    else:
        print('no except/finally between try at',ti,'and loc',loc)
# print region for visual inspection
start=max(1,loc-80)
end=min(len(lines),loc+40)
print('\n--- context lines',start,'to',end,'---')
for i in range(start,end+1):
    print(f"{i:5}: {lines[i-1]}")
