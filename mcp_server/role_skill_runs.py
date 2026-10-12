"""Reviewed role-skill draft runs; consuming Agent authors, Hub stores/checks only."""
import hashlib,json,re,uuid
import core

REGISTRY={
 'ux-copy':{'path':'design/skills/ux-copy/SKILL.md','sha256':'d46a00a62ec637e9ca9d5f7823ea3e0a4ec26dffc53bb5035f59b48ebfcdcde7','cues':['ux','界面文案','状态提示','错误提示','按钮文案'],'inputs':['context','user_state','tone','constraints'],'sections':['Recommended Copy','Alternatives','Rationale','Localization Notes'],'dependencies':'No external tools for supplied-context drafts; brand guide and actual screen constraints must be supplied or marked unknown.'},
 'process-doc':{'path':'operations/skills/process-doc/SKILL.md','sha256':'fceeeddb363e813ba831f9f2de1adb4c7e45ad179366bbb9ea9e40377abe96a9','cues':['sop','流程文档','岗位交接','标准作业','流程说明'],'inputs':['process','owner','steps','exceptions'],'sections':['Process Metadata','Purpose','Scope','RACI Matrix','Process Flow','Detailed Steps','Exceptions and Edge Cases','Metrics','Related Documents'],'dependencies':'No external tools for supplied-context SOP; named people, dates and targets need confirmation. Wiki publishing/project tracker actions are disabled.'},
 'system-design':{'path':'engineering/skills/system-design/SKILL.md','sha256':'8f28eca99f2208872fc2483fcc93326b628f4f73116e91309a95e05da86a0ab5','cues':['系统设计','架构设计','system design','系统工程架构'],'inputs':['problem','functional_requirements','nonfunctional_requirements','constraints'],'sections':['Requirements','High-Level Design','Deep Dive','Scale and Reliability','Trade-off Analysis','Assumptions and Open Questions'],'dependencies':'No external tools for supplied-context architecture; workload, latency and recovery targets must be supplied or left open. No deployment or code execution.'}
}
def skill_id(name):return 'knowledge-work:'+REGISTRY[name]['path']
def adapter_overlay(row):
    spec=REGISTRY['system-design']
    if row['id']==skill_id('system-design') and row['sha256']==spec['sha256']:
        row.update(skill_state='local_text_ready',adapter={'id':'system-design-local-text-v1','required_inputs':spec['inputs'],'output_sections':spec['sections'],'execution':'consuming_agent_text_only'})
    return row
def route(root,task,agent):
    from external_resources import catalog
    if not isinstance(task,str) or not task.strip() or len(task)>8000:raise ValueError('Task must be non-empty text up to 8000 characters')
    visible={e['id']:e for e in catalog(root,agent)}
    candidates=[]
    for name,spec in REGISTRY.items():
        matched=[cue for cue in spec['cues'] if cue.casefold() in task.casefold()]
        if matched:
            e=visible.get(skill_id(name));ready=bool(e and e.get('skill_state')=='local_text_ready' and e['sha256']==spec['sha256'])
            candidates.append({'name':name,'skill_id':skill_id(name),'matched_cues':matched,'ready':ready,'required_inputs':spec['inputs'],'input_type':'non-empty string','required_sections':spec['sections'],'dependencies':spec['dependencies']})
    risky=re.findall(r'发送邮件|发布到|创建工单|生成图片|绘制图片|部署服务|send email|publish to|deploy service',task,re.I)
    return {'status':'needs_scope_clarification' if risky else ('candidates' if candidates else 'no_supported_match'),'candidates':candidates,'action_cues':risky,'routing':'deterministic cues for 3 reviewed skills, not semantic intent recognition','execution':'not_started','executor':'current consuming Agent; no background dispatcher','allowed_capabilities':['local_text_draft'],'instructions':'Choose a matching skill and provide its inputs. External actions are unsupported. If action cues occur, restate a draft-only task; no task is persisted until checks pass.'}
def prepare(root,task,selected_skill,inputs,requested_capabilities,agent):
    from external_resources import prepare_external_skill
    r=route(root,task,agent)
    if requested_capabilities!=['local_text_draft']:return {'status':'blocked','reason':'Only explicitly selected local_text_draft is supported','execution':'not_started'}
    if r['status']!='candidates':return r
    selected=next((c for c in r['candidates'] if c['skill_id']==selected_skill),None)
    if not selected or not selected['ready']:return {'status':'blocked','reason':'Skill not matched, accessible, reviewed or enabled','execution':'not_started'}
    packet=prepare_external_skill(root,selected_skill,inputs,agent)
    if packet['status']!='prepared':return packet
    run_id=uuid.uuid4().hex; folder=root/'90_Assets/role-skill-runs'/run_id;folder.mkdir(parents=True,exist_ok=False)
    packet.update(schema='pkh-role-skill-run-v1',run_id=run_id,owner_agent=agent,natural_task=task,selected_role=selected['name'],role_contract=selected,executor=agent,execution='not_started',dispatch='none; consuming Agent must read source and author the document')
    event=core.capture_event(root,text='Prepared role skill '+selected['name']+' run '+run_id,source_app='role-skill-run',source_agent=agent,explicit=True,event_id='evt_role_'+run_id,raw_paths=[str(folder.relative_to(root))],access={'context_scopes':['work'],'allowed_agents':[agent],'denied_agents':[]})
    packet['source_event_id']=event['event_id'];core.atomic_write_json(folder/'prepared.json',packet)
    return packet
def load(root,run_id,agent):
    from external_resources import contained,resource,read_external_resource
    if not re.fullmatch('[a-f0-9]{32}',run_id):raise ValueError('Invalid run id')
    folder=contained(root,'90_Assets/role-skill-runs/'+run_id);p=json.loads((folder/'prepared.json').read_text(encoding='utf-8'))
    if p['owner_agent']!=agent:raise PermissionError('Run belongs to another agent')
    core.get_event(root,p['source_event_id'],agent_id=agent)
    entry=resource(root,p['skill_id'],agent)
    if entry['skill_state']!='local_text_ready':raise PermissionError('Skill unavailable')
    if entry['sha256']!=p['source']['sha256']:raise ValueError('Source version changed')
    read_external_resource(root,p['skill_id'],agent,max_chars=1)
    return folder,p
def structural_check(packet,sections,review):
    errors=[];required=packet['role_contract']['required_sections']
    for key in required:
        if not isinstance(sections.get(key),str) or not sections[key].strip():errors.append('Missing section: '+key)
    if not isinstance(review,list):return errors+['Review must be an array']
    seen=set()
    for item in review:
        if not isinstance(item,dict):errors.append('Review item must be object');continue
        section=item.get('section')
        if not isinstance(section,str) or section not in required:errors.append('Unknown review section');continue
        if section in seen:errors.append('Duplicate review section: '+section)
        seen.add(section)
        if item.get('status') not in ('met','gap','not_applicable'):errors.append('Invalid review status')
        if not isinstance(item.get('finding'),str) or not item['finding'].strip():errors.append('Missing review finding')
        quote=item.get('evidence_quote')
        body=sections.get(section,'')
        if not isinstance(body,str) or not isinstance(quote,str) or not quote.strip() or quote not in body:errors.append('Review evidence not found in section: '+section)
    for key in set(required)-seen:errors.append('Missing review: '+key)
    return errors
def complete(root,run_id,sections,review,open_items,agent):
    folder,p=load(root,run_id,agent)
    if not isinstance(sections,dict) or not isinstance(open_items,list) or any(not isinstance(x,str) for x in open_items):raise ValueError('Invalid document fields')
    payload={'sections':sections,'review':review,'open_items':open_items}
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode('utf-8')
    if len(raw)>200000:raise ValueError('Document too large')
    errors=structural_check(p,sections,review)
    digest=hashlib.sha256(raw).hexdigest()
    receipt={'run_id':run_id,'skill_id':p['skill_id'],'artifact_sha256':digest,'executor':agent,'execution_evidence':'Agent-submitted authored text; no Hub generation or autonomous dispatch','status':'artifact_recorded' if not errors else 'changes_required','structural_pass':not errors,'errors':errors,'review_type':'same_agent_self_review','semantic_pass':None,'business_execution_verified':False,'recorded_at':core.now_iso(),'artifact_path':str((folder/(digest+'.md')).relative_to(root)).replace('\\','/')}
    with core.FileLock(folder/'write.lock',timeout=10,stale_after=90):
        target=folder/(digest+'.json')
        if target.exists():
            saved=json.loads(target.read_text(encoding='utf-8'))
            if saved['payload']!=payload:raise ValueError('Evidence conflict')
            receipt=saved['receipt']
        else:
            md='# '+p['selected_role']+' 岗位任务产物\n\n任务：'+p['natural_task']+'\n\n执行主体：'+agent+'；来源：'+p['source']['repository']+' / '+p['source']['commit']+' / '+p['source']['source_path']+' / SHA256 '+p['source']['sha256']+'\n\n'
            md+='\n\n'.join('## '+k+'\n\n'+str(v) for k,v in sections.items())
            md+='\n\n## 同一执行Agent自审（不是人工批准）\n\n'+json.dumps(review,ensure_ascii=False,indent=2)+'\n\n## 待决项\n\n'+'\n'.join('- '+x for x in open_items)
            core.atomic_write_text(folder/(digest+'.md'),md);core.atomic_write_json(target,{'payload':payload,'receipt':receipt})
        core.atomic_write_json(folder/'latest.json',receipt)
    return receipt
def get(root,run_id,agent):
    folder,p=load(root,run_id,agent);latest=folder/'latest.json'
    receipt=json.loads(latest.read_text(encoding='utf-8')) if latest.exists() else None
    artifact=None
    if receipt:
        artifact=json.loads((folder/(receipt['artifact_sha256']+'.json')).read_text(encoding='utf-8'))['payload']
    return {'prepared':p,'latest':receipt,'artifact':artifact}
def register_tools(mcp,hub,bound):
    @mcp.tool()
    def route_role_skill_task_tool(task:str)->dict:
        """Match natural task cues to 3 reviewed role skills and expose inputs/dependencies; no execution."""
        return route(hub(),task,bound(require_bound=True))
    @mcp.tool()
    def prepare_role_skill_task_tool(task:str,selected_skill:str,inputs:dict,requested_capabilities:list[str])->dict:
        """Save source-pinned role instructions. Explicitly request ['local_text_draft']; Agent must author next."""
        return prepare(hub(),task,selected_skill,inputs,requested_capabilities,bound(require_bound=True))
    @mcp.tool()
    def record_role_skill_artifact_tool(run_id:str,sections:dict,review:list[dict],open_items:list[str])->dict:
        """Save Agent-authored document plus quoted self-review; structural check is not semantic/business approval."""
        return complete(hub(),run_id,sections,review,open_items,bound(require_bound=True))
    @mcp.tool()
    def get_role_skill_run_tool(run_id:str)->dict:
        """Read own role preparation, actual document and receipt, rechecking ACL and source integrity."""
        return get(hub(),run_id,bound(require_bound=True))
