from pathlib import Path
import json
root=Path('.research-staging/mas-frameworks')
refs={}
for d in root.iterdir():
 if (d/'index.json').exists():
  j=json.loads((d/'index.json').read_text(encoding='utf8'));refs[j['repo']]=j
print('\n'.join(f"{r}: {j['sha']} {j['commit_date']}" for r,j in refs.items()))
