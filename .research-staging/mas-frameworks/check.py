import re,pathlib,json
p=pathlib.Path('research/GitHub调研_MAS框架与科研系统.md');s=p.read_text(encoding='utf8');defined=set(re.findall(r'^\[([^\]]+)\]:',s,re.M));used=set(re.findall(r'\[([A-Z][A-Za-z0-9-]+)\]',s));print('Undefined refs:',used-defined)
j=json.loads(pathlib.Path('.research-staging/mas-frameworks/agentscope-ai__agentscope/index.json').read_text(encoding='utf8'));print([x['path'] for x in j['tree']['tree'] if 'version' in x['path'] and x['path'].startswith('src/')])
