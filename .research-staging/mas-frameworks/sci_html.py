import requests,pathlib,re,json
u='https://github.com/lamm-mit/SciAgentsDiscovery'
r=requests.get(u,timeout=30);print(r.status_code,len(r.content));t=r.text
matches=re.findall(r'"(?:currentOid|oid)":"([0-9a-f]{40})"',t); print(matches[:10])
p=pathlib.Path('.research-staging/mas-frameworks/lamm-mit__SciAgentsDiscovery');p.mkdir(exist_ok=True);(p/'page.html').write_text(t,encoding='utf8')
