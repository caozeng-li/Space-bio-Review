import requests,pathlib
r=pathlib.Path('.research-staging/mas-frameworks/lamm-mit__SciAgentsDiscovery');s='c5c30451b29cba813a11b5ce078909a214dad9f2'
for p in ['ScienceDiscovery/agents.py','ScienceDiscovery/llm_config.py','ScienceDiscovery/graph.py']:
 q=requests.get(f'https://raw.githubusercontent.com/lamm-mit/SciAgentsDiscovery/{s}/{p}',timeout=30);q.raise_for_status();(r/p.replace('/','__')).write_text(q.text,encoding='utf8');print(p,len(q.content))
