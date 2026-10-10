import pathlib,json
r=pathlib.Path('.research-staging/mas-frameworks')
for d in r.iterdir():
 if not (d/'index.json').exists():continue
 j=json.loads((d/'index.json').read_text(encoding='utf8'));paths=[x['path'] for x in j['tree']['tree'] if x['type']=='blob']
 print('\nREPO',j['repo'])
 tokens=['react_agent','dashscope','msghub','assistant.py','group_chat.py','groupchat','state.py','pregel/main.py','meeting.py','agent.py','pyproject.toml','memory','skill']
 selected=[p for p in paths if any(t in p.lower() for t in tokens) and not any(t in p for t in ['test','docs','examples','studio','csharp','dotnet','node_modules'])]
 print('\n'.join(selected[:75]))

