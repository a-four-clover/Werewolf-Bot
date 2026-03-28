from pathlib import Path
p=Path('src') / 'discord_bot.py'
lines=p.read_text(encoding='utf-8').splitlines()
for i in range(1720,1821):
    l=lines[i-1]
    print(f"{i:4} indent={len(l)-len(l.lstrip())} |{l}")
