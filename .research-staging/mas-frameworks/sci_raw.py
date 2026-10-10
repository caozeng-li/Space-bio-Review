import requests,pathlib,json,concurrent.futures
root=pathlib.Path('.research-staging/mas-frameworks/lamm-mit__SciAgentsDiscovery');sha='c5c30451b29cba813a11b5ce078909a214dad9f2'
paths=['README.md','setup.py','Notebooks/SciAgents_ScienceDiscovery_GraphReasoning_automated.ipynb','Notebooks/SciAgents_ScienceDiscovery_GraphReasoning_non-automated.ipynb']
for p in paths:
 r=requests.get(f'https://raw.githubusercontent.com/lamm-mit/SciAgentsDiscovery/{sha}/{p}',timeout=35);r.raise_for_status();(root/p.replace('/','__')).write_text(r.text,encoding='utf8')
 if p.endswith('ipynb'):
  nb=r.json();(root/(p.replace('/','__')+'.txt')).write_text('\n\n'.join(''.join(c['source']) for c in nb['cells'] if c['cell_type']=='code'),encoding='utf8')
 print(p,len(r.content))
