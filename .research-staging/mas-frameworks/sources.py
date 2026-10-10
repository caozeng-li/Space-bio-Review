import pathlib,json,requests,concurrent.futures
root=pathlib.Path('.research-staging/mas-frameworks')
select={
'agentscope-ai/agentscope':['src/agentscope/agent/_agent.py','src/agentscope/model/_dashscope/_model.py','src/agentscope/skill/_base.py','src/agentscope/skill/_local_loader.py'],
'QwenLM/Qwen-Agent':['qwen_agent/agents/assistant.py','qwen_agent/agents/fncall_agent.py','qwen_agent/agents/group_chat.py','qwen_agent/agent.py','qwen_agent/llm/qwen_dashscope.py','qwen_agent/tools/mcp_manager.py'],
'langchain-ai/langgraph':['libs/langgraph/langgraph/graph/state.py','libs/langgraph/langgraph/types.py','libs/langgraph/langgraph/pregel/main.py','libs/langgraph/pyproject.toml'],
'zou-group/virtual-lab':['src/virtual_lab/agent.py','src/virtual_lab/run_meeting.py'],
'huggingface/smolagents':['src/smolagents/agents.py','src/smolagents/models.py'],
'geekan/MetaGPT':['metagpt/roles/role.py','metagpt/environment/base_env.py','metagpt/team.py','metagpt/provider/dashscope_api.py'],
'crewAIInc/crewAI':['lib/crewai/pyproject.toml','lib/crewai/src/crewai/crew.py','lib/crewai/src/crewai/process.py'],
'microsoft/agent-framework':['python/packages/core/pyproject.toml'],
'microsoft/autogen':['python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_selector_group_chat.py']}
for repo in ['agentscope-ai/agentscope','microsoft/agent-framework']:
 j=json.loads((root/repo.replace('/','__')/'index.json').read_text(encoding='utf8'))
 paths=[x['path'] for x in j['tree']['tree'] if x['type']=='blob']
 if repo.startswith('agentscope'):select[repo]+=[p for p in paths if p.startswith('src/agentscope/pipeline/') and p.endswith('.py')]+[p for p in paths if p.startswith('src/agentscope/tool/') and ('_toolkit' in p or 'mcp' in p)]
 else:select[repo]+=[p for p in paths if p.startswith('python/packages/core/agent_framework/_workflows/') and ('group' in p or 'concurrent' in p or 'handoff' in p)]
def f(repo,p):
 d=root/repo.replace('/','__');j=json.loads((d/'index.json').read_text(encoding='utf8'));u=f'https://raw.githubusercontent.com/{repo}/{j["sha"]}/{p}';r=requests.get(u,timeout=30);r.raise_for_status();(d/p.replace('/','__')).write_text(r.text,encoding='utf8');return repo,p,len(r.content)
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
 for ftr in concurrent.futures.as_completed([ex.submit(f,r,p) for r,ps in select.items() for p in ps]):
  try:print(ftr.result())
  except Exception as e:print(str(e))
