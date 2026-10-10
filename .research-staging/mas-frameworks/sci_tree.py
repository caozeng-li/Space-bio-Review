import requests,re,pathlib
r=requests.get('https://github.com/lamm-mit/SciAgentsDiscovery/tree/c5c30451b29cba813a11b5ce078909a214dad9f2/ScienceDiscovery',timeout=30); print(r.status_code)
print('\n'.join(sorted(set(re.findall(r'"path":"([^"]+)"',r.text)))))
