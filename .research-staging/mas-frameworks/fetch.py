import requests,json,pathlib,concurrent.futures
root=pathlib.Path('.research-staging/mas-frameworks')
repos=['agentscope-ai/agentscope','QwenLM/Qwen-Agent','langchain-ai/langgraph','microsoft/autogen','microsoft/agent-framework','crewAIInc/crewAI','huggingface/smolagents','geekan/MetaGPT','zou-group/virtual-lab','lamm-mit/SciAgents','SakanaAI/AI-Scientist']
def f(repo):
 d=root/repo.replace('/','__'); d.mkdir(exist_ok=True)
 r=requests.get('https://api.github.com/repos/'+repo,timeout=30); r.raise_for_status(); info=r.json()
 r=requests.get('https://api.github.com/repos/'+repo+'/commits/'+info['default_branch'],timeout=30); r.raise_for_status(); commit=r.json(); sha=commit['sha']
 r=requests.get('https://api.github.com/repos/'+repo+'/git/trees/'+sha+'?recursive=1',timeout=40); r.raise_for_status(); tree=r.json()
 data={'repo':repo,'sha':sha,'commit_date':commit['commit']['committer']['date'],'archived':info['archived'],'description':info['description'],'branch':info['default_branch'],'tree':tree}
 (d/'index.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
 paths=[x['path'] for x in tree['tree'] if x['type']=='blob']
 targets=[p for p in paths if p.lower() in ['readme.md','pyproject.toml','setup.py','requirements.txt']]
 for p in targets:
  rr=requests.get(f'https://raw.githubusercontent.com/{repo}/{sha}/{p}',timeout=30); (d/p.replace('/','__')).write_text(rr.text,encoding='utf8')
 return repo,sha, data['commit_date'],targets
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
 for future in concurrent.futures.as_completed([ex.submit(f,r) for r in repos]):
  try: print(future.result())
  except Exception as e: print(type(e).__name__,str(e))
