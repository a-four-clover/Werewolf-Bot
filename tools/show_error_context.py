from pathlib import Path
p=Path(r'c:\Users\ytmk1\Desktop\hobbies\recorder\werewolf\src\discord_bot.py')
lines=p.read_text(encoding='utf-8').splitlines()
start=6515
end=6540
for i in range(start, end):
    print(f"{i+1:5}: {lines[i]}")
