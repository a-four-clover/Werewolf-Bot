import ast
from pathlib import Path
p=Path('src')/ 'discord_bot.py'
s=p.read_text(encoding='utf-8')
mod=ast.parse(s)
for node in ast.walk(mod):
    if isinstance(node, ast.Try):
        handlers=len(node.handlers)
        final=len(node.finalbody)
        if handlers==0 and final==0:
            print('Try node at lineno', node.lineno, 'has no except or finally')
            # print snippet
            start=max(1,node.lineno-3)
            lines=s.splitlines()
            for i in range(start, min(start+10, len(lines)+1)):
                print(f"{i}: {lines[i-1]}")
            print('---')
print('done')
