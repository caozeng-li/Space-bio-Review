import requests,pathlib
u='https://raw.githubusercontent.com/agentscope-ai/agentscope/72f3f6fa0b2fc38b8517f408ab616f0f2bd229e6/src/agentscope/_version.py'
r=requests.get(u,timeout=20);print(r.status_code,r.text);pathlib.Path('.research-staging/mas-frameworks/agentscope-ai__agentscope/src__agentscope___version.py').write_text(r.text,encoding='utf8')
