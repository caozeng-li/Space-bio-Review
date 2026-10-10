exec(open('.research-staging/mas-frameworks/fetch.py',encoding='utf-8-sig').read().split('with concurrent.futures')[0])
print(f('lamm-mit/SciAgentsDiscovery'))
j=json.loads((root/'lamm-mit__SciAgentsDiscovery'/'index.json').read_text(encoding='utf8'))
print('\n'.join(x['path'] for x in j['tree']['tree'] if x['type']=='blob'))
