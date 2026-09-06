const $ = (s, el=document) => el.querySelector(s);
function toast(message){
  const el=$('#toast');
  if(!el||!message) return;
  el.textContent=String(message);
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer=setTimeout(()=>el.classList.remove('show'),2800);
}
function apiError(err){
  const raw=String((err&&err.message)||err||'请求失败');
  try{
    const parsed=JSON.parse(raw);
    const detail=parsed.detail;
    if(typeof detail==='string') return detail;
    if(Array.isArray(detail)&&detail.length) return detail.map(item=>item.msg||item.message||JSON.stringify(item)).join('；');
    if(detail&&detail.message) return detail.message;
    if(parsed.message) return parsed.message;
  }catch(e){}
  return raw.slice(0,180);
}
const authState = {token: localStorage.getItem('af_token')||'', me:null};
const pagePerm = {dashboard:'',sessions:'session:read',studio:'trace:read',traces:'trace:read',evaluations:'eval:read',playground:'agent:write',agents:'agent:read',workflows:'workflow:read',mcp:'mcp:read',skills:'skill:read',models:'model:read',sandboxes:'sandbox:read',roles:'role:read'};
function can(perm){
  if(!perm) return !!authState.me;
  const granted=authState.me?.permissions||[];
  if(granted.includes('*')||granted.includes('platform:admin')) return true;
  if(granted.includes(perm)) return true;
  if(perm.endsWith(':read')&&granted.includes(perm.slice(0,-5)+':write')) return true;
  if((perm==='eval:read'||perm==='eval:write')&&granted.includes('eval:run')) return true;
  if(perm==='role:read'&&(granted.includes('user:read')||granted.includes('tenant:admin'))) return true;
  if(perm==='agent:write'&&granted.includes('session:write')) return true;
  return false;
}
function authHeaders(extra={}){
  const headers={...extra};
  if(authState.token) headers.Authorization='Bearer '+authState.token;
  const tenant=localStorage.getItem('af_tenant_id');
  if(tenant) headers['X-Tenant-Id']=tenant;
  return headers;
}
function showLogin(message=''){
  const gate=$('#loginGate');
  if(!gate) return;
  gate.hidden=false;
  const err=$('#loginError');
  if(err){err.hidden=!message;err.textContent=message||''}
}
function hideLogin(){const gate=$('#loginGate'); if(gate) gate.hidden=true}
async function logout(){
  const token=authState.token;
  try{
    if(token) await fetch('/api/auth/logout',{method:'POST',headers:authHeaders()});
  }catch(e){}
  authState.token='';
  authState.me=null;
  localStorage.removeItem('af_token');
  localStorage.removeItem('af_tenant_id');
  if($('#userName')) $('#userName').textContent='未登录';
  if($('#userRole')) $('#userRole').textContent='请先登录';
  if($('#userAvatar')) $('#userAvatar').textContent='?';
  if($('#tenantSwitch')) {$('#tenantSwitch').hidden=true;$('#tenantSwitch').innerHTML=''}
  if($('#content')) $('#content').innerHTML='';
  if($('#crumbTitle')) $('#crumbTitle').textContent='/ 请登录';
  if(typeof closeSessionDetail==='function') closeSessionDetail();
  if($('#loginPass')) $('#loginPass').value='';
  showLogin();
}
function applyMe(me){
  authState.me=me;
  if($('#userName')) $('#userName').textContent=me.display_name||me.username;
  if($('#userRole')) $('#userRole').textContent=(me.tenant_name||'')+' · '+(me.role_name||'成员');
  if($('#userAvatar')) $('#userAvatar').textContent=(me.display_name||me.username||'?').slice(0,1);
  document.querySelectorAll('.nav-item').forEach(btn=>{
    btn.hidden=!can(pagePerm[btn.dataset.page]||'');
  });
  const sw=$('#tenantSwitch');
  if(sw){
    const tenants=me.tenants||[];
    sw.hidden=tenants.length<2;
    sw.innerHTML=tenants.map(t=>`<option value="${t.id}" ${Number(t.id)===Number(me.tenant_id)?'selected':''}>${escapeHtml(t.name)}</option>`).join('');
  }
}
const api = async (path, options={}) => {
  options.headers = authHeaders(options.headers||{});
  const r = await fetch(path, options);
  if(r.status===401){ authState.token=''; localStorage.removeItem('af_token'); showLogin('请先登录'); throw new Error('请先登录'); }
  if(!r.ok) throw new Error(await r.text());
  if(r.status===204) return {};
  const text=await r.text();
  return text?JSON.parse(text):{};
};
const fmt = n => n >= 1000000 ? (n/1000000).toFixed(2)+'M' : n >= 1000 ? (n/1000).toFixed(1)+'K' : n;
const dt = value => new Date(value+'Z').toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const statusText = {completed:'已完成',running:'运行中',failed:'失败',ok:'正常',error:'异常',published:'已发布',draft:'草稿',queued:'排队中',passed:'通过',skipped:'跳过',cancelled:'已取消'};
const titles = {dashboard:'运行概览',sessions:'会话查询',studio:'AgentScope Studio',traces:'AgentScope Studio',evaluations:'数据测试',playground:'Agent 调试台',agents:'Agent 管理',workflows:'Agent 编排',mcp:'MCP 工具',skills:'Skill 管理',models:'模型配置',sandboxes:'沙箱管理',roles:'权限管理'};
const pageMeta = {
  sessions:['SESSION EXPLORER','会话查询','检索和审计所有 Agent 会话记录'], studio:['AGENTSCOPE STUDIO','AgentScope Studio','查看 Agent 运行轨迹、Token 消耗与调试视图'], traces:['AGENTSCOPE STUDIO','AgentScope Studio','查看 Agent 运行轨迹、Token 消耗与调试视图'], evaluations:['EVALUATION','数据测试','用标准数据集持续验证 Agent 质量'], playground:['AGENT PLAYGROUND','Agent 调试台','每个 Agent 使用独立工作空间保存会话、链路和配置'], agents:['AGENT REGISTRY','Agent 管理','管理 Agent 配置、版本与发布状态'], workflows:['ORCHESTRATION','Agent 编排','通过拖拽组合多 Agent 协作流程'], mcp:['TOOL REGISTRY','MCP 工具','集中配置和管控 MCP 服务与工具'], skills:['CAPABILITY HUB','Skill 管理','人工添加可复用的 Agent 专业能力'], models:['MODEL GATEWAY','模型配置','填写模型供应商、API 密钥与推理参数'], sandboxes:['SECURE RUNTIME','沙箱管理','隔离 Agent 的代码和工具执行环境'], roles:['ACCESS CONTROL','权限管理','基于角色控制平台资源访问权限']
};
let currentPage='dashboard', currentParam='';
function pageTools(extra=''){
  return `<div class="page-actions">${extra}<button type="button" class="btn ghost" onclick="refreshPage()">刷新</button></div>`;
}
async function refreshPage(){
  return render(currentPage, currentParam);
}
async function afterChange(page, message){
  if(message) toast(message);
  return render(page||currentPage, page===currentPage?currentParam:'');
}
function head(page, action=''){const m=pageMeta[page];return `<div class="page-head"><div><div class="eyebrow">${m[0]}</div><h1>${m[1]}</h1><p>${m[2]}</p></div>${action}</div>`}
function pill(s){return `<span class="pill ${s}">${statusText[s]||s}</span>`}
async function dashboard(){const d=await api('/api/dashboard');const m=d.metrics;return `${head('sessions', pageTools()).replace('会话查询','运行概览').replace('检索和审计所有 Agent 会话记录','实时掌握 Agent 服务的运行状态与业务表现')}
<div class="metric-grid">
${metric('今日请求',fmt(m.requests),'↗ 12.6%','较昨日','#2367e8','↗')}${metric('成功率',m.success_rate+'%','↗ 1.2%','近 24 小时','#16a56a','✓')}${metric('平均延迟',(m.avg_latency_ms/1000).toFixed(2)+'s','↘ 8.3%','响应更快','#7457e8','⌁')}${metric('Token 消耗',fmt(m.tokens),'↗ 6.8%','今日累计','#e49b18','T')}
</div><div class="dashboard-grid"><section class="panel"><div class="panel-title"><h3>请求趋势</h3><small>近 24 小时 · 每小时</small></div><div class="chart-wrap">${d.activity.map(v=>`<i class="bar" style="height:${v/3.8}px"></i>`).join('')}</div></section><section class="panel"><div class="panel-title"><h3>Agent 健康度</h3><small>${d.agents.length} 个在线</small></div><div class="agent-list">${d.agents.map((a,i)=>`<div class="agent-row"><div class="agent-icon">${a.name[0]}</div><div><b>${a.name}</b><small>${a.model_name} · ${a.version}</small><div class="progress"><i style="width:${a.success_rate}%"></i></div></div><span class="score">${a.success_rate}%</span></div>`).join('')}</div></section></div>${sessionTable(d.recent_sessions,true)}`}
function metric(label,value,trend,sub,color,icon){return `<div class="metric-card" style="--accent:${color}"><div class="metric-label">${label}<span class="metric-icon">${icon}</span></div><div class="metric-value">${value}</div><div class="trend">${trend}<span>${sub}</span></div></div>`}
function sessionTable(rows, dashboard=false){const body=rows.length?rows.map(x=>`<tr class="session-row" ${dashboard?'':`data-session-id="${escapeHtml(x.session_id)}" tabindex="0"`}><td class="mono">${escapeHtml(x.session_id)}</td><td><b>${escapeHtml(x.title)}</b><br><small style="color:#9aa3b0">${escapeHtml(x.channel)}</small></td><td>${escapeHtml(x.agent_name)}</td><td class="mono">${escapeHtml(x.user_id)}</td><td>${pill(x.status)}</td><td>${x.message_count}</td><td>${fmt(x.total_tokens)}</td><td>${(x.latency_ms/1000).toFixed(2)}s</td><td>${dt(x.updated_at||x.created_at)}</td>${dashboard?'':`<td><button type="button" class="btn ghost session-view" data-session-id="${escapeHtml(x.session_id)}" onclick="event.stopPropagation();openSessionDetail('${escapeHtml(x.session_id)}')">查看</button></td>`}</tr>`).join(''):`<tr><td class="session-empty" colspan="${dashboard?9:10}">暂无真实会话。请先在 Agent 调试台发起一次对话。</td></tr>`;return `<section class="panel wide-panel"><div class="panel-title"><h3>${dashboard?'最近会话':'真实会话记录'}</h3><small>${rows.length} 条记录</small></div><div style="overflow:auto"><table class="data-table"><thead><tr><th>Session ID</th><th>会话主题</th><th>Agent</th><th>用户</th><th>状态</th><th>消息</th><th>Token</th><th>耗时</th><th>时间</th>${dashboard?'':'<th>操作</th>'}</tr></thead><tbody>${body}</tbody></table></div></section>`}
async function sessions(){const [rows,agents]=await Promise.all([api('/api/sessions'),api('/api/agents')]);return `${head('sessions', pageTools())}<section class="panel"><div class="table-tools"><div class="search"><input id="sessionQ" placeholder="搜索 Session ID、用户或消息内容"></div><select id="agentFilter" class="select"><option value="">全部 Agent</option>${agents.map(x=>`<option>${escapeHtml(x.name)}</option>`).join('')}</select><select id="statusFilter" class="select"><option value="">全部状态</option><option value="completed">已完成</option><option value="running">运行中</option><option value="failed">失败</option></select><button class="btn primary" id="doFilter">查询</button></div><div id="sessionResults">${sessionTable(rows).replace('<section class="panel wide-panel">','<section>')}</div></section>`}

function sessionDetailMarkup(data){const messages=(data.messages||[]).map(item=>`<article class="session-message ${item.role==='user'?'user':'assistant'}"><header><b>${item.role==='user'?'用户':escapeHtml(item.agent_name||data.agent_name)}</b><time>${dt(item.created_at)}</time></header><p>${escapeHtml(item.content).replace(/\n/g,'<br>')}</p></article>`).join('');const traces=(data.traces||[]).map(item=>`<div class="session-trace"><span class="mono">${escapeHtml(item.trace_id)}</span>${pill(item.status)}<span>${item.duration_ms} ms</span><span>${fmt((item.input_tokens||0)+(item.output_tokens||0))} Token</span></div>`).join('');return `<section class="session-detail"><div class="session-detail-head"><div><small>SESSION DETAIL</small><h3>${escapeHtml(data.title)}</h3><p class="mono">${escapeHtml(data.session_id)}</p></div><button type="button" class="close" id="closeSessionDetail" aria-label="关闭">×</button></div><div class="session-facts"><span><small>Agent</small><b>${escapeHtml(data.agent_name)}</b></span><span><small>用户</small><b>${escapeHtml(data.user_id)}</b></span><span><small>状态</small>${pill(data.status)}</span><span><small>Token</small><b>${fmt(data.total_tokens)}</b></span><span><small>总耗时</small><b>${data.latency_ms} ms</b></span></div><h4>真实消息记录 · ${(data.messages||[]).length}</h4><div class="session-messages">${messages||'<div class="session-detail-empty">该历史会话没有保存消息正文。</div>'}</div><h4>执行链路 · ${(data.traces||[]).length}</h4><div class="session-traces">${traces||'<div class="session-detail-empty">暂无关联链路。</div>'}</div></section>`}
function closeSessionDetail(){
  const modal=$('#sessionModal');
  const target=$('#sessionDetail');
  if(modal&&modal.open) modal.close();
  if(target) target.innerHTML='';
  document.querySelectorAll('.session-row.active').forEach(row=>row.classList.remove('active'));
}
async function openSessionDetail(sessionId){
  const modal=$('#sessionModal');
  const target=$('#sessionDetail');
  if(!modal||!target){toast('详情窗口未就绪');return}
  document.querySelectorAll('.session-row').forEach(row=>row.classList.toggle('active', row.dataset.sessionId===sessionId));
  target.innerHTML='<div class="loading session-detail-loading">正在读取真实会话…</div>';
  if(!modal.open) modal.showModal();
  try{
    const data=await api('/api/sessions/'+encodeURIComponent(sessionId));
    target.innerHTML=sessionDetailMarkup(data);
    const close=$('#closeSessionDetail');
    if(close) close.onclick=closeSessionDetail;
  }catch(e){target.innerHTML='<div class="empty">会话详情加载失败。</div>'}
}

const resourceInfo={agents:{icon:'◇',name:'Agent',desc:x=>x.description,meta:x=>[x.model_name+' · '+(x.version||''), x.workspace?('空间 '+x.workspace):agentBindSummary(x)],action:'新建 Agent'},mcp:{icon:'⚙',name:'MCP 服务',desc:x=>x.endpoint,meta:x=>[mcpTransportLabel(x.transport),(x.runnable?'可调用 ':'')+(x.tools_count||0)+' 个工具'],action:'添加 MCP'},skills:{icon:'✦',name:'Skill',desc:x=>x.description,meta:x=>[x.version,x.has_instruction?'指令已就绪':'待填写指令'],action:'添加 Skill'},models:{icon:'◉',name:'模型',desc:x=>x.model_id,meta:x=>[x.provider,x.has_credential?'密钥已就绪':'待填写密钥'],action:'添加模型'},sandboxes:{icon:'▣',name:'沙箱策略',desc:x=>x.runtime+' · '+(x.backend||'local'),meta:x=>[x.cpu_limit+' / '+x.memory_limit,(x.network_mode==='deny'?'断网隔离':x.network_mode)+' · '+(x.timeout_seconds||60)+'s'],action:'新建策略'},roles:{icon:'♙',name:'角色',desc:x=>x.description,meta:x=>[x.user_count+' 位用户',(x.permissions||[]).length+' 项权限'],action:'新建角色'}};
const resourceStore = {};
function resourceActions(page,x){const writable=x.editable!==false;const primary=page==='agents'?`<button type="button" class="btn primary resource-edit" onclick="openPlayground(${x.id})">调试运行</button>`:page==='models'?`<button type="button" class="btn primary resource-edit" ${x.enabled?'':"disabled title='请先启用模型'"} onclick="testModel(${x.id})">连通测试</button>`:page==='mcp'?`<button type="button" class="btn primary resource-edit" ${x.enabled?'':"disabled title='请先启用 MCP'"} onclick="testMcp(${x.id})">探测工具</button>`:page==='skills'?`<button type="button" class="btn primary resource-edit" ${x.enabled?'':"disabled title='请先启用 Skill'"} onclick="testSkill(${x.id})">预览指令</button>`:page==='sandboxes'?`<button type="button" class="btn primary resource-edit" ${x.enabled?'':"disabled title='请先启用沙箱'"} onclick="testSandbox(${x.id})">试跑代码</button>`:'';return `${primary}${writable?`<button type="button" class="btn ghost resource-edit" onclick="openEdit('${page}',${x.id})">编辑</button><button type="button" class="btn ghost resource-edit danger" onclick="removeResource('${page}',${x.id})">删除</button>`:'<span class="muted">只读</span>'}`}
function statusControl(page,x){if(x.enabled===undefined)return '';return `<button type="button" class="switch ${x.enabled?'on':''}" role="switch" aria-checked="${x.enabled}" aria-label="${x.enabled?'停用':'启用'}${x.name}" title="点击${x.enabled?'停用':'启用'}" onclick="toggleEnabled('${page}',${x.id},${!x.enabled})"><i></i></button>`}
function agentBindSummary(x){
  const skills=(x.bound_skills||[]).map(item=>item.name);
  const tools=(x.bound_mcps||[]).flatMap(item=>(item.tools||[]).map(tool=>tool.name));
  if(!skills.length && !tools.length){
    const skillCount=(x.skill_ids||[]).length, mcpCount=(x.mcp_ids||[]).length;
    return skillCount||mcpCount?`${skillCount} 技能 · ${mcpCount} 个 MCP`:'未关联技能或工具';
  }
  return [skills.length?skills.join('、'):null, tools.length?tools.length+' 个工具':null].filter(Boolean).join(' · ');
}
function agentTags(x){
  const skills=x.bound_skills||(x.skill_ids||[]).map(id=>resourceStore.skills&&resourceStore.skills[id]).filter(Boolean);
  const mcps=x.bound_mcps||(x.mcp_ids||[]).map(id=>resourceStore.mcp&&resourceStore.mcp[id]).filter(Boolean);
  const tools=mcps.flatMap(item=>(item.tools||[]).map(tool=>tool.name||tool));
  if(!skills.length && !tools.length && !mcps.length) return `<div class="agent-tags"><i class="tag empty">未关联技能或工具</i></div>`;
  return `<div class="agent-tags">${skills.map(item=>`<i class="tag skill">${escapeHtml(item.name||item)}</i>`).join('')}${tools.length?tools.map(name=>`<i class="tag tool">${escapeHtml(name)}</i>`).join(''):mcps.map(item=>`<i class="tag tool">${escapeHtml(item.name)}</i>`).join('')}</div>`;
}
async function iam(){
  const [roles, users, me] = await Promise.all([api('/api/roles'), api('/api/users'), api('/api/auth/me')]);
  resourceStore.roles=Object.fromEntries(roles.map(x=>[x.id,x]));
  resourceStore.users=Object.fromEntries(users.map(x=>[x.id,x]));
  authState.me=me;
  const catalog = me.catalog||[];
  const canUser=can('user:write')||can('tenant:admin');
  return `${head('roles', pageTools(can('role:write')?`<button class="btn primary" onclick="openCreate('roles')">＋ 新建角色</button>`:''))}<div class="iam-grid">
    <section class="panel"><div class="panel-title"><h3>租户成员 · ${users.length}</h3><small>${escapeHtml(me.tenant_name||'')}</small></div>
      <div style="overflow:auto"><table class="data-table"><thead><tr><th>用户</th><th>角色</th><th>状态</th>${canUser?'<th>操作</th>':''}</tr></thead><tbody>
      ${users.map(x=>`<tr><td><b>${escapeHtml(x.display_name||x.username)}</b><br><small class="mono">${escapeHtml(x.username)}</small></td><td>${escapeHtml(x.role_name||'-')}</td><td>${x.enabled?'启用':'停用'}</td>${canUser?`<td><button type="button" class="btn ghost" onclick="openUserEdit(${x.id})">编辑</button></td>`:''}</tr>`).join('')}
      </tbody></table></div></section>
    <section class="panel"><div class="panel-title"><h3>角色 · ${roles.length}</h3><small>权限按 resource:action，写包含读</small></div>
      ${roles.map(x=>`<article class="resource-card" style="margin-bottom:10px"><div class="resource-head"><div class="resource-logo">♙</div><div><h3>${escapeHtml(x.name)}</h3><p>${escapeHtml(x.description||'')}</p></div></div><div class="perm-list">${(x.permissions||[]).map(p=>`<i>${escapeHtml(p)}</i>`).join('')}</div><div class="resource-meta"><span>${x.user_count||0} 位用户</span><span class="resource-ops">${x.editable!==false&&can('role:write')?`<button type="button" class="btn ghost" onclick="openEdit('roles',${x.id})">编辑</button>`:'只读'}</span></div></article>`).join('')}
    </section>
  </div>
  <section class="panel" style="margin-top:16px"><div class="panel-title"><h3>权限目录</h3><small>对齐 AgentScope：属主默认可编辑，同租户按角色共享，跨租户拒绝</small></div>
    <div class="perm-list">${catalog.map(item=>`<i title="${escapeHtml(item.group)}">${escapeHtml(item.key)} ${escapeHtml(item.label)}</i>`).join('')}</div>
  </section>`;
}
async function resources(page){
  const rows=await api('/api/'+page),info=resourceInfo[page];
  resourceStore[page]=Object.fromEntries(rows.map(x=>[x.id,x]));
  if(page==='agents'){
    const extras=await Promise.all([api('/api/mcp'),api('/api/skills')]);
    resourceStore.mcp=Object.fromEntries(extras[0].map(x=>[x.id,x]));
    resourceStore.skills=Object.fromEntries(extras[1].map(x=>[x.id,x]));
  }
  return `${head(page, pageTools(`<button class="btn primary" onclick="openCreate('${page}')">＋ ${info.action}</button>`))}<div class="resource-grid">${rows.map(x=>`<article class="resource-card ${x.enabled===false?'resource-disabled':''}"><div class="resource-head"><div class="resource-logo">${info.icon}</div><div style="min-width:0"><h3>${x.name}</h3><p>${info.desc(x)||'暂无说明'}</p></div>${statusControl(page,x)}</div>${page==='agents'?agentTags(x):''}<div class="resource-meta"><span>${info.meta(x)[0]}</span><span>${info.meta(x)[1]}</span><span class="resource-ops">${resourceActions(page,x)}</span></div></article>`).join('')}</div>`;
}
const evalState={tab:'datasets',datasetId:'',runId:'',poll:null};
const scorerLabel={contains:'包含匹配',exact:'完全匹配',regex:'正则',llm:'LLM 判分'};
async function evaluations(){
  const [datasets,runs,agents,models]=await Promise.all([
    api('/api/datasets'),api('/api/evaluations'),api('/api/agents'),api('/api/models')
  ]);
  evalState.catalog={datasets,runs,agents,models};
  const tabs=[['datasets','数据集'],['runs','测试任务'],['report','报告']].map(([id,label])=>`<button type="button" class="eval-tab ${evalState.tab===id?'active':''}" data-eval-tab="${id}">${label}</button>`).join('');
  return `${head('evaluations', pageTools(`<a class="btn ghost" href="/api/datasets/template.csv">下载模板</a><button class="btn primary" onclick="evalOpenLaunch()">＋ 创建测试</button>`))}<div class="eval-tabs">${tabs}</div><div id="evalBody">${evalBodyHtml()}</div>`;
}
function evalBodyHtml(){
  if(evalState.tab==='datasets') return evalDatasetsHtml();
  if(evalState.tab==='report') return evalReportHtml();
  return evalRunsHtml();
}
function evalDatasetsHtml(){
  const rows=evalState.catalog.datasets||[];
  const current=rows.find(x=>String(x.id)===String(evalState.datasetId))||rows[0];
  if(current) evalState.datasetId=String(current.id);
  const side=rows.length?rows.map(x=>`<button type="button" class="eval-ds ${String(x.id)===String(evalState.datasetId)?'active':''}" data-ds="${x.id}"><b>${escapeHtml(x.name)}</b><small>${x.case_count||0} 条用例</small></button>`).join(''):'<div class="empty">还没有数据集</div>';
  return `<div class="eval-grid"><aside class="eval-side">${side}<button type="button" class="btn ghost" style="width:100%;margin-top:8px" onclick="evalCreateDataset()">＋ 新建数据集</button></aside><section class="eval-main" id="evalDatasetMain">${current?`<div class="loading">正在读取用例…</div>`:'<div class="empty">先新建或导入一个数据集。</div>'}</section></div>`;
}
function evalRunsHtml(){
  const rows=evalState.catalog.runs||[];
  const body=rows.length?rows.map(x=>`<tr><td><b>${escapeHtml(x.name)}</b><br><small>${x.mode==='online'?'在线抽检':'离线回归'} · ${scorerLabel[x.scorer]||x.scorer}</small></td><td>${escapeHtml(x.dataset||'')}</td><td>${escapeHtml(x.agent_name||'')}</td><td>${pill(x.status)}</td><td>${x.passed||0}/${x.total||x.cases||0}</td><td><b style="color:${x.score>=80?'#16a56a':'#e49b18'}">${x.status==='completed'?(x.score+'%'):'—'}</b></td><td><button class="btn ghost" onclick="evalOpenReport(${x.id})">报告</button> <button class="btn ghost" onclick="evalRerun(${x.id})">重跑</button>${x.status==='running'||x.status==='queued'?` <button class="btn ghost" onclick="evalCancel(${x.id})">取消</button>`:''}</td></tr>`).join(''):`<tr><td class="session-empty" colspan="7">还没有测试任务。导入数据集后点右上角创建测试。</td></tr>`;
  return `<section class="panel"><table class="data-table"><thead><tr><th>测试任务</th><th>数据集</th><th>Agent</th><th>状态</th><th>进度</th><th>通过率</th><th>操作</th></tr></thead><tbody>${body}</tbody></table></section>`;
}
function evalReportHtml(){
  return `<section class="panel" id="evalReportMain"><div class="empty">${evalState.runId?'正在载入报告…':'从测试任务里点「报告」查看结果。'}</div></section>`;
}
function evalPaint(){
  const body=$('#evalBody');
  if(body) body.innerHTML=evalBodyHtml();
  bindEvalChrome();
}
function bindEvalChrome(){
  document.querySelectorAll('[data-eval-tab]').forEach(btn=>{
    btn.onclick=()=>{
      evalStopPoll();
      evalState.tab=btn.dataset.evalTab;
      evalPaint();
      evalHydrate();
    };
  });
  document.querySelectorAll('[data-ds]').forEach(btn=>{
    btn.onclick=()=>evalSelectDataset(btn.dataset.ds);
  });
}
function evalHydrate(){
  if(evalState.tab==='datasets'&&evalState.datasetId) evalLoadDataset(evalState.datasetId);
  if(evalState.tab==='report'&&evalState.runId) evalLoadReport(evalState.runId);
}
function resetModalSubmit(label){
  const btn=$('#modalSubmit');
  if(!btn) return null;
  btn.hidden=false;
  btn.disabled=false;
  btn.type='submit';
  btn.onclick=null;
  btn.dataset.busy='';
  if(label) btn.textContent=label;
  return btn;
}
function evalOpenModal(title,submit,fieldsHtml,page,wide=false){
  $('#modalEyebrow').textContent='数据测试';
  $('#modalTitle').textContent=title;
  resetModalSubmit(submit);
  $('#modal').classList.toggle('modal-wide',!!wide);
  $('#modalFields').innerHTML=fieldsHtml;
  $('#modalForm').dataset.page=page;
  $('#modalForm').dataset.id='';
  $('#modalForm').noValidate=true;
  $('#modal').showModal();
}
async function evalOpenDataset(id){return evalSelectDataset(id)}
async function evalSelectDataset(id){
  evalState.datasetId=String(id);
  evalState.tab='datasets';
  evalPaint();
  await evalLoadDataset(id);
}
async function evalLoadDataset(id){
  const main=$('#evalDatasetMain');
  if(!main) return;
  const loadSeq=evalState.loadSeq=(evalState.loadSeq||0)+1;
  try{
    const ds=await api('/api/datasets/'+id);
    if(loadSeq!==evalState.loadSeq) return;
    const hit=(evalState.catalog.datasets||[]).find(x=>Number(x.id)===Number(id));
    if(hit) hit.case_count=ds.case_count;
    const cases=ds.cases||[];
    main.innerHTML=`<div class="eval-toolbar"><div><b>${escapeHtml(ds.name)}</b><div class="muted">${ds.case_count||0} 条 · ${escapeHtml(ds.source_name||'手动添加')}</div></div><input type="file" id="evalFile" accept=".csv,.json,.jsonl,text/csv,application/json" hidden><button class="btn ghost" onclick="$('#evalFile').click()">导入文件</button><button class="btn ghost" onclick="evalAddCase(${ds.id})">添加一条</button><button class="btn ghost danger" onclick="evalDeleteDataset(${ds.id})">删除数据集</button></div>
    <table class="data-table"><thead><tr><th>编号</th><th>输入</th><th>期望</th><th></th></tr></thead><tbody>${cases.length?cases.map(c=>`<tr><td class="mono">${escapeHtml(c.case_key||c.id)}</td><td>${escapeHtml(c.input)}</td><td>${escapeHtml(c.expected||'—')}</td><td><button class="btn ghost" onclick="evalDeleteCase(${ds.id},${c.id})">删除</button></td></tr>`).join(''):'<tr><td colspan="4" class="session-empty">还没有用例。在下方填写后点「保存用例」，或导入 CSV/JSONL。</td></tr>'}</tbody></table>
    <form class="eval-add" id="evalAddForm"><div><label>用户输入 / 问题</label><textarea name="input" required placeholder="发给 Agent 的问题"></textarea></div><div><label>期望答案（可留空）</label><textarea name="expected" placeholder="用于包含/完全/正则匹配"></textarea></div><button class="btn primary" type="submit">保存用例</button></form>`;
    const file=$('#evalFile');
    if(file) file.onchange=()=>evalImportFile(ds.id,file);
    const add=$('#evalAddForm');
    if(add) add.onsubmit=async ev=>{
      ev.preventDefault();
      const data=Object.fromEntries(new FormData(add));
      if(!String(data.input||'').trim()){toast('请填写用户输入');return}
      try{
        await api(`/api/datasets/${ds.id}/cases`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({input:String(data.input).trim(),expected:data.expected||''})});
        toast('用例已保存');
        evalState.datasetId=String(ds.id);
        await evalReloadThen('datasets');
      }catch(err){toast(apiError(err)||'保存用例失败')}
    };
  }catch(e){
    if(loadSeq!==evalState.loadSeq) return;
    main.innerHTML=`<div class="empty">数据集读取失败：${escapeHtml(apiError(e))}</div>`;
  }
}
async function evalReloadThen(tab, opener){
  evalState.tab=tab;
  await render('evaluations');
  if(opener) await opener();
}
function evalCreateDataset(){
  evalOpenModal('新建数据集','创建',`<div class="field"><label>名称</label><input name="name" required maxlength="120" placeholder="例如 客服回归集"></div><div class="field"><label>说明</label><input name="description" placeholder="可选"></div>`,'eval-dataset');
}
function evalAddCase(datasetId){
  evalOpenModal('添加用例','保存',`<input type="hidden" name="dataset_id" value="${datasetId}"><div class="field"><label>用户输入 / 问题</label><textarea name="input" class="skill-md" required placeholder="发给 Agent 的问题"></textarea></div><div class="field"><label>期望答案</label><textarea name="expected" class="skill-md" placeholder="可留空"></textarea></div>`,'eval-case');
}
async function evalSubmitDataset(form){
  const data=Object.fromEntries(new FormData(form));
  const name=String(data.name||'').trim();
  if(!name){toast('请填写数据集名称');return}
  const row=await api('/api/datasets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description:data.description||''})});
  $('#modal').close();
  toast('数据集已创建，请添加用例');
  evalState.datasetId=String(row.id);
  await evalReloadThen('datasets');
}
async function evalSubmitCase(form){
  const data=Object.fromEntries(new FormData(form));
  const input=String(data.input||'').trim();
  const datasetId=Number(data.dataset_id||evalState.datasetId);
  if(!input){toast('请填写用户输入');return}
  if(!datasetId){toast('请先选择数据集');return}
  await api(`/api/datasets/${datasetId}/cases`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({input,expected:data.expected||''})});
  $('#modal').close();
  toast('用例已保存');
  evalState.datasetId=String(datasetId);
  await evalReloadThen('datasets');
}
async function evalDeleteCase(datasetId,caseId){
  if(!confirm('删除这条用例？')) return;
  try{
    await api(`/api/datasets/${datasetId}/cases/${caseId}`,{method:'DELETE'});
    toast('用例已删除');
    evalState.datasetId=String(datasetId);
    await evalReloadThen('datasets');
  }catch(err){toast(apiError(err)||'删除失败')}
}
async function evalDeleteDataset(datasetId){
  if(!confirm('删除整个数据集？')) return;
  try{
    await api('/api/datasets/'+datasetId,{method:'DELETE'});
    evalState.datasetId='';
    toast('数据集已删除');
    await evalReloadThen('datasets');
  }catch(err){toast(apiError(err)||'删除失败')}
}
async function evalImportFile(datasetId,input){
  const file=input.files&&input.files[0];
  if(!file) return;
  const body=new FormData();
  body.append('file',file);
  body.append('dataset_id',String(datasetId));
  body.append('on_duplicate','skip');
  try{
    const r=await fetch('/api/datasets/import',{method:'POST',headers:authHeaders(),body});
    const data=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error((data.detail&&data.detail.message)||(typeof data.detail==='string'?data.detail:data.message)||'导入失败');
    toast(`导入完成：新增 ${data.added||0}，跳过 ${data.skipped||0}`);
    evalState.datasetId=String(datasetId);
    await evalReloadThen('datasets');
  }catch(e){toast(e.message||'导入失败')}
  input.value='';
}
function evalOpenLaunch(){
  const datasets=evalState.catalog.datasets||[];
  const agents=evalState.catalog.agents||[];
  const models=evalState.catalog.models||[];
  const usable=datasets.filter(x=>(x.case_count||0)>0);
  if(!agents.length){toast('请先在 Agent 管理里创建一个 Agent');return}
  if(!datasets.length){toast('请先新建数据集并添加至少一条用例');return}
  if(!usable.length){toast('数据集还是空的，先在下方保存至少一条用例');return}
  const preferred=usable.find(x=>String(x.id)===String(evalState.datasetId))||usable[0];
  evalOpenModal('创建测试','开始测试',`<div class="eval-form">
    <div class="field"><label>Agent</label><select class="select" style="width:100%" name="agent_id">${agents.map(x=>`<option value="${x.id}">${escapeHtml(x.name)}</option>`).join('')}</select></div>
    <div class="field"><label>数据集</label><select class="select" style="width:100%" name="dataset_id">${usable.map(x=>`<option value="${x.id}" ${Number(x.id)===Number(preferred.id)?'selected':''}>${escapeHtml(x.name)} · ${x.case_count||0} 条</option>`).join('')}</select></div>
    <div class="field"><label>测试方式</label><select class="select" style="width:100%" name="mode"><option value="online">在线抽检（同步，最多 10 条）</option><option value="offline">离线回归（后台跑完全集）</option></select></div>
    <div class="field"><label>打分方式</label><select class="select" style="width:100%" name="scorer" id="evalScorer"><option value="contains">包含匹配</option><option value="exact">完全匹配</option><option value="regex">正则</option><option value="llm">LLM 判分</option></select></div>
    <div class="field wide" id="evalJudgeField" hidden><label>裁判模型</label><select class="select" style="width:100%" name="judge_model_id"><option value="">使用 Agent 自己的模型</option>${models.map(x=>`<option value="${x.id}">${escapeHtml(x.name)}</option>`).join('')}</select></div>
    <div class="field wide"><label>任务名称</label><input name="name" placeholder="可留空，自动生成"></div>
  </div>`,'evaluations',true);
  const scorer=$('#evalScorer');
  if(scorer) scorer.onchange=()=>{$('#evalJudgeField').hidden=scorer.value!=='llm'};
  const btn=resetModalSubmit('开始测试');
  if(!btn) return;
  btn.type='button';
  btn.onclick=async ev=>{
    ev.preventDefault();
    if(btn.dataset.busy==='1') return;
    btn.dataset.busy='1';
    btn.disabled=true;
    btn.textContent='正在测试…';
    toast('正在发起测试');
    try{
      await evalLaunchFromForm($('#modalForm'));
    }catch(err){
      toast(apiError(err)||'创建测试失败，请检查 Agent 与数据集');
    }finally{
      if(btn.isConnected && $('#modal')&&$('#modal').open){
        btn.dataset.busy='';
        btn.disabled=false;
        btn.textContent='开始测试';
      }
    }
  };
}
async function evalLaunchFromForm(form){
  const data=Object.fromEntries(new FormData(form));
  const agentId=Number(data.agent_id);
  const datasetId=Number(data.dataset_id);
  if(!agentId||!datasetId){toast('请选择 Agent 和带用例的数据集');return}
  const selected=(evalState.catalog.datasets||[]).find(x=>Number(x.id)===datasetId);
  if(selected&&!(selected.case_count||0)){toast('这个数据集没有用例，无法开测');return}
  const payload={agent_id:agentId,dataset_id:datasetId,scorer:data.scorer||'contains',name:data.name||'',judge_model_id:data.judge_model_id?Number(data.judge_model_id):null};
  const online=data.mode!=='offline';
  const row=await api(online?'/api/evaluations/online':'/api/evaluations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  $('#modal').close();
  toast(online?'在线测试完成':'离线任务已进入队列');
  evalState.runId=row.id;
  await evalReloadThen('report');
}
async function evalOpenReport(id){
  evalState.tab='report';
  evalState.runId=id;
  evalPaint();
  await evalLoadReport(id);
}
async function evalLoadReport(id){
  const main=$('#evalReportMain'); if(!main) return;
  try{
    const run=await api('/api/evaluations/'+id);
    const rows=run.results||[];
    const idx=(evalState.catalog.runs||[]).findIndex(x=>x.id===id);
    if(idx>=0) evalState.catalog.runs[idx]=run;
    const running=run.status==='running'||run.status==='queued';
    main.innerHTML=`<div class="eval-toolbar"><div><b>${escapeHtml(run.name)}</b><div class="muted">${escapeHtml(run.agent_name)} · ${escapeHtml(run.dataset)} · ${scorerLabel[run.scorer]||run.scorer} · ${pill(run.status)}</div></div><a class="btn ghost" href="/api/evaluations/${run.id}/export.csv">导出 CSV</a><button class="btn ghost" onclick="evalRerun(${run.id})">按同样配置重跑</button></div>
    <div class="eval-metrics">
      <div class="eval-metric"><span>通过率</span><b>${run.status==='completed'||run.judged?run.score+'%':'—'}</b></div>
      <div class="eval-metric"><span>通过 / 失败 / 跳过</span><b>${run.passed||0} / ${run.failed||0} / ${run.skipped||0}</b></div>
      <div class="eval-metric"><span>平均延迟</span><b>${run.avg_latency_ms||0} ms</b></div>
      <div class="eval-metric"><span>Token</span><b>${run.total_tokens||0}</b></div>
    </div>
    ${run.error_message?`<p class="muted">${escapeHtml(run.error_message)}</p>`:''}
    <table class="data-table"><thead><tr><th>状态</th><th>输入</th><th>期望</th><th>实际输出</th><th>说明</th></tr></thead><tbody>${rows.length?rows.map(x=>`<tr><td>${pill(x.status)}</td><td>${escapeHtml(x.input)}</td><td>${escapeHtml(x.expected||'—')}</td><td class="eval-actual">${escapeHtml(x.actual||'')}</td><td>${escapeHtml(x.reason||x.error||'')}${x.trace_id?`<br><small class="mono">${escapeHtml(x.trace_id)}</small>`:''}</td></tr>`).join(''):`<tr><td colspan="5" class="session-empty">${running?'正在执行…':'暂无结果'}</td></tr>`}</tbody></table>`;
    if(running) evalStartPoll(id); else evalStopPoll();
  }catch(e){main.innerHTML='<div class="empty">报告加载失败</div>'}
}
function evalStartPoll(id){
  evalStopPoll();
  evalState.poll=setInterval(()=>{if(evalState.tab==='report'&&String(evalState.runId)===String(id))evalLoadReport(id);else evalStopPoll()},2000);
}
function evalStopPoll(){if(evalState.poll){clearInterval(evalState.poll);evalState.poll=null}}
async function evalRerun(id){
  const row=await api(`/api/evaluations/${id}/run`,{method:'POST'});
  toast(row.mode==='online'?'已重新跑完':'已重新入队');
  evalState.runId=id;
  await evalReloadThen('report', ()=>evalLoadReport(id));
}
async function evalCancel(id){
  await api(`/api/evaluations/${id}/cancel`,{method:'POST'});
  toast('已取消');
  await afterChange('evaluations');
}
function bindEvalPage(){
  bindEvalChrome();
  evalHydrate();
}
async function traces(){return studio()}
async function studio(){
  const obs=await api('/api/observability').catch(()=>({studio:{}}));
  const st=obs.studio||{};
  const url=st.url||'http://127.0.0.1:3000';
  const open=`<a class="btn primary" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">在新窗口打开</a>`;
  if(st.reachable){
    return `${head('studio', pageTools(open))}<div class="studio-shell"><div class="studio-frame-wrap"><iframe class="studio-frame" title="AgentScope Studio" src="${escapeHtml(url)}"></iframe></div></div>`;
  }
  return `${head('studio', pageTools(open))}<div class="studio-shell"><div class="studio-empty">
    <b>还没有连上 AgentScope Studio</b>
    <p>本页会嵌入 Studio 的运行轨迹和 Token 视图。先在本机启动 Studio，并在 <code>.env</code> 填写 <code>AGENTSCOPE_STUDIO_URL</code>。</p>
    <pre>npx @agentscope/studio</pre>
    <p>当前地址 ${escapeHtml(url)}</p>
  </div></div>`;
}
let pgCatalog={agents:[],models:[],mcps:[],skills:[]};
async function playground(selectedAgent=''){
  const [agents,allModels,mcps,skills]=await Promise.all([api('/api/agents'),api('/api/models'),api('/api/mcp'),api('/api/skills')]);
  const models=allModels.filter(x=>x.enabled);
  const liveMcps=(mcps||[]).filter(x=>x.enabled!==false);
  const liveSkills=(skills||[]).filter(x=>x.enabled!==false);
  pgCatalog={agents,models,mcps:liveMcps,skills:liveSkills};
  if(selectedAgent) chatState.agentId=String(selectedAgent);
  if(!chatState.agentId && agents[0]) chatState.agentId=String(agents[0].id);
  if(!chatState.modelId && models[0]) chatState.modelId=String(models[0].id);
  await restoreAgentChat(chatState.agentId);
  const agent=agents.find(x=>String(x.id)===String(chatState.agentId))||agents[0];
  const model=models.find(x=>String(x.id)===String(chatState.modelId))||models[0];
  return `${head('playground', pageTools('<button class="btn ghost" type="button" id="clearChat">新开会话</button>'))}
<div class="pg-shell">
  <div class="pg-toolbar">
    <label>Agent<select id="runAgent" class="select">${agents.map(x=>`<option value="${x.id}" ${String(x.id)===String(agent&&agent.id)?'selected':''}>${x.name}</option>`).join('')}</select></label>
    <label>模型<select id="runModel" class="select">${models.map(x=>`<option value="${x.id}" ${String(x.id)===String(model&&model.id)?'selected':''}>${x.name}</option>`).join('')}</select></label>
    <div class="pg-bind" id="pgBindHint"></div>
    ${models.length?'':`<div class="inline-warning">没有已启用的模型，请先到模型配置中启用。</div>`}
  </div>
  <div class="pg-split">
    <section class="wechat-stage">
      <header class="wechat-head">
        <div class="wechat-peer"><i class="wechat-avatar agent" id="chatAvatar">${(agent&&agent.name||'A')[0]}</i><div><b id="chatPeerName">${agent?agent.name:'未选择 Agent'}</b><small id="chatPeerMeta">${model?model.name+' · '+model.model_id:'请选择模型'}</small></div></div>
        <span id="runState" class="pill draft">待发送</span>
      </header>
      <div class="wechat-log" id="chatLog"></div>
      <form class="wechat-composer" id="chatForm">
        <textarea id="runMessage" rows="1" placeholder="输入消息，Enter 发送，Shift+Enter 换行" ${models.length?'':'disabled'}></textarea>
        <button type="submit" id="runButton" class="wechat-send" ${models.length?'':'disabled'}>发送</button>
      </form>
    </section>
    <aside class="pg-trace">
      <header class="pg-trace-head">
        <div><h3>执行链路</h3><small id="traceMeta">发送消息后，这里会按步骤写入当前 Agent 工作空间</small></div>
        <small class="lf-link" id="workspacePath">${agent&&agent.workspace?escapeHtml(agent.workspace):''}</small>
      </header>
      <div id="traceList" class="trace-timeline"></div>
    </aside>
  </div>
</div>`
}
const WF_KINDS = {
  start: {icon:'▶', title:'开始节点', hint:'流程入口'},
  agent: {icon:'◇', title:'Agent 节点', hint:'AgentScope Agent'},
  mcp: {icon:'⚙', title:'MCP 工具', hint:'调用 MCP 工具'},
  condition: {icon:'⌘', title:'条件路由', hint:'按条件分流'},
  parallel: {icon:'◫', title:'并行分支', hint:'并行执行'},
  end: {icon:'■', title:'结束节点', hint:'流程结束'}
};
const wfState = {id:null, name:'', status:'draft', description:'', nodes:[], edges:[], selected:null, agents:[], mcp:[], skills:[], linking:null, dirty:false};
let wfDrag = null;
let wfUid = 0;
let wfGlobalsBound = false;
function wfKindOf(type){
  if(WF_KINDS[type]) return type;
  return {'开始节点':'start','Agent 节点':'agent','MCP 工具':'mcp','条件路由':'condition','并行分支':'parallel','结束节点':'end'}[type] || 'agent';
}
function loadWfRow(row){
  const g = (row && row.graph) || {nodes:[], edges:[]};
  const nodes = (g.nodes||[]).map((n,i)=>{
    const type = wfKindOf(n.type);
    return {
      id: String(n.id || ('n'+i)),
      type,
      label: n.label || WF_KINDS[type].title,
      x: Number.isFinite(n.x) ? n.x : 40 + i*170,
      y: Number.isFinite(n.y) ? n.y : (i%2 ? 120 : 210),
      agent: n.agent || (type==='agent' ? (n.label||'') : ''),
      mcp: n.mcp || '',
      note: n.note || '',
      policy: n.policy || 'retry'
    };
  });
  wfUid = nodes.reduce((max,n)=>{
    const num = Number(String(n.id).replace(/\D/g,''));
    return Number.isFinite(num) ? Math.max(max, num) : max;
  }, 0);
  wfState.id = row ? row.id : null;
  wfState.name = row ? row.name : '未命名流程';
  wfState.status = row ? row.status : 'draft';
  wfState.description = row ? (row.description||'') : '';
  wfState.nodes = nodes;
  wfState.edges = (g.edges||[]).map(e=>({source:String(e.source), target:String(e.target)}));
  wfState.selected = nodes[0] ? nodes[0].id : null;
  wfState.linking = null;
  wfState.dirty = false;
}
function selectedWfNode(){return wfState.nodes.find(n=>n.id===wfState.selected)}
function wfEdgeMarkup(){
  return wfState.edges.map(edge=>{
    const a = wfState.nodes.find(n=>n.id===edge.source);
    const b = wfState.nodes.find(n=>n.id===edge.target);
    if(!a||!b) return '';
    const x1=a.x+150, y1=a.y+28, x2=b.x, y2=b.y+28, mx=(x1+x2)/2;
    return `<path d="M${x1} ${y1} C${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}" fill="none" stroke="#8aa4d4" stroke-width="2"/>`;
  }).join('');
}
function paintWorkflow(){
  const canvas = $('#canvas');
  if(!canvas) return;
  const hint = wfState.linking ? '点击目标节点左侧圆点完成连线 · ' : '拖左侧组件到画布，或点节点右侧圆点连线 · ';
  const nodes = wfState.nodes.map(n=>{
    const kind = WF_KINDS[n.type] || WF_KINDS.agent;
    return `<div class="node ${n.id===wfState.selected?'active':''}" data-id="${n.id}" style="left:${n.x}px;top:${n.y}px"><i class="wf-port in" data-port="in" data-id="${n.id}"></i><b>${escapeHtml(n.label)}</b><small>${kind.hint}</small><i class="wf-port out" data-port="out" data-id="${n.id}"></i></div>`;
  }).join('');
  canvas.innerHTML = `<span class="canvas-note">${hint}${escapeHtml(wfState.name||'未命名流程')}${wfState.dirty?' · 未保存':''}</span><svg class="wf-edges" viewBox="0 0 2400 1200" preserveAspectRatio="none">${wfEdgeMarkup()}</svg>${nodes}`;
  paintInspector();
}
function paintInspector(){
  const box = $('#wfInspector');
  if(!box) return;
  const n = selectedWfNode();
  if(!n){
    box.innerHTML = '<h3>节点配置</h3><p class="wf-empty-hint">从左侧拖入节点，或点击画布上的节点进行配置。</p>';
    return;
  }
  const agents = `<option value="">未绑定</option>` + wfState.agents.map(a=>`<option value="${escapeHtml(a.name)}" ${a.name===n.agent?'selected':''}>${escapeHtml(a.name)}</option>`).join('');
  const mcps = `<option value="">未绑定</option>` + wfState.mcp.map(a=>`<option value="${escapeHtml(a.name)}" ${a.name===n.mcp?'selected':''}>${escapeHtml(a.name)}</option>`).join('');
  box.innerHTML = `<h3>节点配置</h3>
    <label>节点名称</label><input id="wfNodeLabel" value="${escapeHtml(n.label)}">
    ${n.type==='agent'?`<label>绑定 Agent</label><select id="wfNodeAgent" class="select" style="width:100%">${agents}</select>`:''}
    ${n.type==='mcp'?`<label>绑定 MCP</label><select id="wfNodeMcp" class="select" style="width:100%">${mcps}</select>`:''}
    <label>失败策略</label>
    <select id="wfNodePolicy" class="select" style="width:100%">
      <option value="retry" ${n.policy==='retry'?'selected':''}>重试 2 次</option>
      <option value="abort" ${n.policy==='abort'?'selected':''}>中断流程</option>
      <option value="skip" ${n.policy==='skip'?'selected':''}>跳过继续</option>
    </select>
    <label>节点说明</label><textarea id="wfNodeNote">${escapeHtml(n.note)}</textarea>
    <p class="wf-empty-hint">点节点右侧圆点，再点另一节点左侧圆点即可连线。</p>
    <div class="wf-insp-actions"><button type="button" class="btn ghost danger" id="wfDelNode">删除节点</button></div>`;
  const bind = (sel, fn)=>{const el=$(sel); if(el) el.oninput = el.onchange = fn};
  bind('#wfNodeLabel', e=>{n.label=e.target.value; wfState.dirty=true; const title=$(`.node[data-id="${n.id}"] b`); if(title) title.textContent=n.label});
  bind('#wfNodeAgent', e=>{n.agent=e.target.value; wfState.dirty=true});
  bind('#wfNodeMcp', e=>{n.mcp=e.target.value; wfState.dirty=true});
  bind('#wfNodePolicy', e=>{n.policy=e.target.value; wfState.dirty=true});
  bind('#wfNodeNote', e=>{n.note=e.target.value; wfState.dirty=true});
  if($('#wfDelNode')) $('#wfDelNode').onclick=()=>deleteWfNode(n.id);
}
function deleteWfNode(id){
  wfState.nodes = wfState.nodes.filter(n=>n.id!==id);
  wfState.edges = wfState.edges.filter(e=>e.source!==id && e.target!==id);
  wfState.selected = wfState.nodes[0] ? wfState.nodes[0].id : null;
  wfState.dirty = true;
  paintWorkflow();
}
function addWfNode(kind, x, y){
  const meta = WF_KINDS[kind] || WF_KINDS.agent;
  wfUid += 1;
  const node = {id:'n'+wfUid, type:kind, label:meta.title, x:Math.max(20, x-75), y:Math.max(40, y-25), agent:'', mcp:'', note:'', policy:'retry'};
  wfState.nodes.push(node);
  wfState.selected = node.id;
  wfState.dirty = true;
  paintWorkflow();
}
function connectWf(from, to){
  if(!from || !to || from===to) return;
  if(wfState.edges.some(e=>e.source===from && e.target===to)) {wfState.linking=null; paintWorkflow(); return}
  wfState.edges.push({source:from, target:to});
  wfState.linking = null;
  wfState.dirty = true;
  paintWorkflow();
}
function canvasPoint(e, canvas){
  const rect = canvas.getBoundingClientRect();
  return {x:e.clientX-rect.left+canvas.scrollLeft, y:e.clientY-rect.top+canvas.scrollTop};
}
function refreshWfEdges(){
  const svg = $('#canvas .wf-edges');
  if(svg) svg.innerHTML = wfEdgeMarkup();
}
async function saveWorkflow(publish){
  const graph = {nodes:wfState.nodes, edges:wfState.edges};
  const status = publish ? 'published' : 'draft';
  try{
    if(!wfState.id){
      const row = await api('/api/workflows', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:wfState.name||('新流程 '+Date.now()), description:wfState.description||'', status, graph})});
      wfState.id = row.id;
      wfState.name = row.name;
    }else{
      await api('/api/workflows/'+wfState.id, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({graph, status})});
    }
    wfState.status = status;
    wfState.dirty = false;
    await afterChange('workflows', publish?'工作流已发布':'草稿已保存');
  }catch(e){toast('保存失败，请稍后重试')}
}
async function createWorkflow(){
  const name = '新流程 '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  try{
    const row = await api('/api/workflows', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, description:'', status:'draft', graph:{nodes:[], edges:[]}})});
    wfState.id = row.id;
    await afterChange('workflows', '已创建空白流程');
  }catch(e){toast('创建失败，名称可能重复')}
}
function ensureWfGlobals(){
  if(wfGlobalsBound) return;
  wfGlobalsBound = true;
  window.addEventListener('mousemove', e=>{
    if(!wfDrag || !$('#canvas')) return;
    const n = wfState.nodes.find(x=>x.id===wfDrag.id);
    if(!n) return;
    n.x = Math.max(8, e.clientX - wfDrag.ox);
    n.y = Math.max(36, e.clientY - wfDrag.oy);
    wfDrag.moved = true;
    wfState.dirty = true;
    const el = $(`.node[data-id="${n.id}"]`);
    if(el){el.style.left=n.x+'px'; el.style.top=n.y+'px'}
    refreshWfEdges();
  });
  window.addEventListener('mouseup', ()=>{wfDrag=null});
  window.addEventListener('keydown', e=>{
    if((e.key!=='Delete' && e.key!=='Backspace') || !$('#canvas')) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if(tag==='INPUT' || tag==='TEXTAREA' || tag==='SELECT') return;
    if(wfState.selected){e.preventDefault(); deleteWfNode(wfState.selected)}
  });
}
function bindWorkflowCanvas(){
  const canvas = $('#canvas');
  if(!canvas) return;
  ensureWfGlobals();
  paintWorkflow();
  document.querySelectorAll('.draggable').forEach(el=>{
    el.addEventListener('dragstart', e=>{
      e.dataTransfer.setData('text/plain', el.dataset.kind);
      e.dataTransfer.effectAllowed = 'copy';
    });
    el.addEventListener('dblclick', ()=>addWfNode(el.dataset.kind, 80+wfState.nodes.length*24, 160));
  });
  canvas.addEventListener('dragover', e=>e.preventDefault());
  canvas.addEventListener('drop', e=>{
    e.preventDefault();
    const kind = wfKindOf(e.dataTransfer.getData('text/plain'));
    const p = canvasPoint(e, canvas);
    addWfNode(kind, p.x, p.y);
  });
  canvas.addEventListener('mousedown', e=>{
    const port = e.target.closest('.wf-port');
    if(port){
      e.preventDefault();
      const id = port.dataset.id;
      if(port.dataset.port==='in' && wfState.linking) connectWf(wfState.linking, id);
      else if(port.dataset.port==='out'){wfState.linking=id; paintWorkflow()}
      return;
    }
    const nodeEl = e.target.closest('.node');
    if(!nodeEl){
      if(wfState.linking){wfState.linking=null; paintWorkflow()}
      return;
    }
    const id = nodeEl.dataset.id;
    const n = wfState.nodes.find(x=>x.id===id);
    if(!n) return;
    wfState.selected = id;
    document.querySelectorAll('#canvas .node').forEach(el=>el.classList.toggle('active', el.dataset.id===id));
    paintInspector();
    wfDrag = {id, ox:e.clientX-n.x, oy:e.clientY-n.y, moved:false};
    e.preventDefault();
  });
  if($('#wfSave')) $('#wfSave').onclick=()=>saveWorkflow(false);
  if($('#wfPublish')) $('#wfPublish').onclick=()=>saveWorkflow(true);
  if($('#wfNew')) $('#wfNew').onclick=createWorkflow;
  if($('#wfSelect')) $('#wfSelect').onchange=async()=>{
    const id = Number($('#wfSelect').value);
    const rows = await api('/api/workflows');
    const row = rows.find(x=>x.id===id);
    if(row){loadWfRow(row); paintWorkflow()}
  };
}
async function workflows(){
  const [rows, agents, mcp, skills] = await Promise.all([api('/api/workflows'), api('/api/agents'), api('/api/mcp'), api('/api/skills')]);
  wfState.agents = agents;
  wfState.mcp = mcp;
  wfState.skills = skills;
  const current = rows.find(x=>x.id===wfState.id) || rows[0];
  loadWfRow(current || null);
  const kinds = Object.entries(WF_KINDS);
  const options = rows.map(x=>`<option value="${x.id}" ${x.id===wfState.id?'selected':''}>${escapeHtml(x.name)}</option>`).join('');
  return `${head('workflows', `<div class="wf-toolbar">
    <select id="wfSelect" class="select"${rows.length?'':' disabled'}>${options||'<option>暂无流程</option>'}</select>
    <button class="btn ghost" type="button" id="wfNew">＋ 新建流程</button>
    <button class="btn ghost" type="button" onclick="refreshPage()">刷新</button>
    <button class="btn ghost" type="button" id="wfSave">保存草稿</button>
    <button class="btn primary" type="button" id="wfPublish">发布流程</button>
  </div>`)}<div class="designer"><aside class="palette"><h3>节点组件</h3>${kinds.map(([k,v])=>`<div class="draggable" draggable="true" data-kind="${k}"><span class="drag-icon">${v.icon}</span>${v.title}</div>`).join('')}<h3 style="margin-top:25px">已用资源</h3><small style="color:#8994a4;line-height:1.8">${agents.length} 个 Agent<br>${skills.length} 个 Skill<br>${mcp.length} 个 MCP 工具</small></aside><div class="canvas" id="canvas"></div><aside class="inspector" id="wfInspector"></aside></div>`;
}
const forms={
  agents:[['name','Agent 名称','合同审核助手'],['description','功能说明','说明 Agent 的业务职责'],['model_name','使用模型','Qwen-Max'],['version','初始版本','v1.0.0'],['system_prompt','系统提示词','你是一名专业的企业助手']],
  mcp:[['name','服务名称','高德地图'],['transport','传输协议','streamable_http'],['endpoint','服务地址','https://mcp.amap.com/mcp']],
  skills:[['name','Skill 名称','客服回复规范'],['description','能力说明','这段指令会注入到调试台的 Agent'],['version','版本','1.0.0'],['instruction','SKILL.md 正文','# 技能名称\n\n直接在这里写 Markdown。调试台会按这份正文执行。']],
  models:[['name','配置名称','生产模型'],['provider','供应商','OpenAI / DeepSeek'],['model_id','模型 ID','deepseek-v4-flash'],['base_url','Base URL','https://api.deepseek.com'],['api_key','API 密钥','sk-...'],['temperature','Temperature','0.2']],
  sandboxes:[['name','策略名称','受限 Python 沙箱'],['runtime','运行镜像','python:3.11'],['cpu_limit','CPU 限制','1 vCPU'],['memory_limit','内存限制','1 GiB'],['timeout_seconds','超时时间（秒）','60'],['network_mode','网络模式','deny']],
  roles:[['name','角色名称','业务观察员'],['description','角色说明','只读查看业务数据'],['permissions','权限列表（逗号分隔）','session:read,trace:read']]
};
function mcpTransportLabel(value){
  const kind=String(value||'').toLowerCase().replace(/-/g,'_');
  if(kind==='streamable_http'||kind==='http'||kind==='http_stream') return 'HTTP Stream';
  if(kind==='sse') return 'SSE';
  if(kind==='stdio') return 'StdIO';
  return String(value||'').toUpperCase();
}
function mcpTransportValue(value){
  const kind=String(value||'').toLowerCase().replace(/-/g,'_');
  if(kind==='streamable_http'||kind==='http'||kind==='http_stream') return 'streamable_http';
  if(kind==='sse') return 'sse';
  return 'stdio';
}
function sandboxFormHtml(row){
  const mode=String(row&&row.network_mode||'deny');
  return `<div class="field"><label>策略名称</label><input name="name" placeholder="受限 Python 沙箱" value="${row?escapeHtml(row.name):''}" required></div>
    <div class="field"><label>运行时</label>
      <select class="select" style="width:100%" name="runtime">
        <option value="python:3.11" ${!row||row.runtime==='python:3.11'?'selected':''}>本地隔离 · python:3.11</option>
        <option value="python:3.12" ${row&&row.runtime==='python:3.12'?'selected':''}>本地隔离 · python:3.12</option>
        <option value="agentscope/runtime-sandbox-base" ${row&&String(row.runtime||'').includes('runtime-sandbox')?'selected':''}>AgentScope Runtime（Docker）</option>
      </select>
    </div>
    <div class="field"><label>CPU 限制</label><input name="cpu_limit" placeholder="1 vCPU" value="${row?escapeHtml(row.cpu_limit||'1 vCPU'):'1 vCPU'}"></div>
    <div class="field"><label>内存限制</label><input name="memory_limit" placeholder="1 GiB" value="${row?escapeHtml(row.memory_limit||'1 GiB'):'1 GiB'}"></div>
    <div class="field"><label>超时（秒）</label><input name="timeout_seconds" type="number" min="1" max="3600" value="${row?escapeHtml(String(row.timeout_seconds||60)):'60'}"></div>
    <div class="field"><label>网络</label>
      <select class="select" style="width:100%" name="network_mode">
        <option value="deny" ${mode==='deny'?'selected':''}>deny · 断网隔离</option>
        <option value="allowlist" ${mode==='allowlist'?'selected':''}>allowlist · 受限联网</option>
        <option value="allow" ${mode==='allow'?'selected':''}>allow · 允许联网</option>
      </select>
      <small class="bind-hint">保存后点「试跑代码」会真正执行 print(1+1)。有 AgentScope Runtime / Docker 时优先走官方沙箱，否则用本机隔离进程。</small>
    </div>`;
}
function mcpFormHtml(row){
  const transport=mcpTransportValue(row&&row.transport||'streamable_http');
  const endpoint=row?escapeHtml(row.endpoint||''):'';
  const hasAuth=!!(row&&row.config&&((row.config.headers&&(row.config.headers.Authorization||row.config.headers.authorization))||row.config.api_key||row.config.token));
  return `<div class="field"><label>服务名称</label><input name="name" placeholder="高德地图" value="${row?escapeHtml(row.name):''}" required></div>
    <div class="field"><label>传输协议</label>
      <select class="select" style="width:100%" name="transport" id="mcpTransport" required>
        <option value="streamable_http" ${transport==='streamable_http'?'selected':''}>HTTP Stream</option>
        <option value="sse" ${transport==='sse'?'selected':''}>SSE</option>
        <option value="stdio" ${transport==='stdio'?'selected':''}>StdIO</option>
      </select>
    </div>
    <div class="field"><label id="mcpEndpointLabel">${transport==='stdio'?'启动命令':'服务地址'}</label>
      <input name="endpoint" id="mcpEndpoint" placeholder="${transport==='stdio'?'builtin:local-tools':'https://mcp.example.com/mcp'}" value="${endpoint}" required>
    </div>
    <div class="field" id="mcpAuthField" ${transport==='stdio'?'hidden':''}><label>请求头 / Token（可选）</label>
      <input name="auth" type="password" placeholder="${hasAuth?'已保存密钥，留空则不修改':'Bearer sk-... 或 Authorization: Bearer sk-...'}" autocomplete="off">
      <small class="bind-hint">HTTP Stream 走 MCP Streamable HTTP。保存后点「探测工具」会握手并拉工具列表。</small>
    </div>`;
}
function bindMcpForm(){
  const sel=$('#mcpTransport'), input=$('#mcpEndpoint'), label=$('#mcpEndpointLabel'), auth=$('#mcpAuthField');
  if(!sel||!input) return;
  const apply=()=>{
    const stream=sel.value!=='stdio';
    if(label) label.textContent=stream?'服务地址':'启动命令';
    input.placeholder=stream?'https://mcp.example.com/mcp':'builtin:local-tools';
    if(auth) auth.hidden=!stream;
  };
  sel.onchange=apply;
  apply();
}
function bindEmpty(page, label){
  return `<div class="bind-empty-card">还没有可关联的${label}。<button type="button" class="bind-link" data-jump="${page}">去添加</button></div>`;
}
function bindPicker(kind, name, rows, selected, emptyPage, emptyLabel){
  const ids=(selected||[]).map(Number);
  const live=rows.filter(x=>x.enabled!==false);
  if(!live.length) return bindEmpty(emptyPage, emptyLabel);
  const options=live.map(x=>{
    const tools=x.tools||[];
    const meta=kind==='mcp'
      ? `${mcpTransportLabel(x.transport)} · ${x.endpoint||'MCP'} · ${tools.length||x.tools_count||0} 个工具`
      : `${x.description||'Skill'} · ${x.version||''}`;
    return `<label class="bind-option" data-search="${escapeHtml(`${x.name} ${meta}`.toLowerCase())}">
      <input type="checkbox" name="${name}" value="${x.id}" ${ids.includes(x.id)?'checked':''}>
      <span class="bind-icon${kind==='skill'?' skill':''}">${kind==='skill'?'✦':'⚙'}</span>
      <span class="bind-copy"><b>${escapeHtml(x.name)}</b><small>${escapeHtml(meta)}</small></span>
    </label>`;
  }).join('');
  return `<div class="bind-picker" data-bind="${kind}">
    <button type="button" class="bind-picker-toggle" aria-expanded="false">
      <span class="bind-picker-summary"></span>
      <span class="bind-picker-caret" aria-hidden="true"></span>
    </button>
    <div class="bind-picker-panel">
      ${live.length>=6?`<input type="search" class="bind-picker-search" placeholder="搜索${emptyLabel}" autocomplete="off">`:''}
      <div class="bind-picker-list">${options}</div>
    </div>
  </div>`;
}
function agentFormHtml(row, models, mcpRows, skillRows, sandboxRows){
  const modelOptions=models.map(m=>`<option value="${escapeHtml(m.name)}" ${row&&row.model_name===m.name?'selected':''}>${escapeHtml(m.name)} · ${escapeHtml(m.model_id)}</option>`).join('');
  const sandboxOptions=`<option value="">不使用沙箱</option>`+(sandboxRows||[]).filter(x=>x.enabled!==false||Number(x.id)===Number(row&&row.sandbox_id)).map(x=>`<option value="${x.id}" ${Number(row&&row.sandbox_id)===Number(x.id)?'selected':''}>${escapeHtml(x.name)} · ${escapeHtml(x.runtime||'')} · ${x.network_mode==='deny'?'断网':'可联网'}</option>`).join('');
  return `<div class="agent-form">
    <div class="agent-form-grid">
      <div class="field"><label>Agent 名称</label><input name="name" placeholder="合同审核助手" value="${row?escapeHtml(row.name):''}" required></div>
      <div class="field"><label>使用模型</label><select class="select" style="width:100%" name="model_name" required>${modelOptions}</select></div>
      <div class="field"><label>功能说明</label><input name="description" placeholder="说明 Agent 的业务职责" value="${row?escapeHtml(row.description||''):''}"></div>
      <div class="field"><label>初始版本</label><input name="version" placeholder="v1.0.0" value="${row?escapeHtml(row.version||''):''}"></div>
      <div class="field"><label>执行沙箱</label><select class="select" style="width:100%" name="sandbox_id">${sandboxOptions}</select></div>
      ${row&&row.workspace?`<div class="field"><label>工作空间</label><input value="${escapeHtml(row.workspace)}" readonly></div>`:''}
    </div>
    <div class="field"><label>系统提示词</label><textarea name="system_prompt" class="agent-prompt" placeholder="你是一名专业的企业助手">${row?escapeHtml(row.system_prompt||''):''}</textarea></div>
    <section class="bind-section">
      <div class="bind-head"><h3>关联 MCP 工具</h3><small id="bindMcpCount"></small></div>
      <p class="bind-hint">点开后滚动勾选，选中的工具会提供给当前 Agent。</p>
      ${bindPicker('mcp','mcp_ids',mcpRows,row&&row.mcp_ids,'mcp','MCP 工具')}
    </section>
    <section class="bind-section">
      <div class="bind-head"><h3>关联技能</h3><small id="bindSkillCount"></small></div>
      <p class="bind-hint">点开后滚动勾选，技能说明会写入系统提示词。</p>
      ${bindPicker('skill','skill_ids',skillRows,row&&row.skill_ids,'skills','技能')}
    </section>
  </div>`;
}
function updateBindCounts(){
  const form=$('#modalForm'); if(!form) return;
  form.querySelectorAll('.bind-picker').forEach(picker=>{
    const boxes=[...picker.querySelectorAll('input[type="checkbox"]')];
    const checked=boxes.filter(x=>x.checked);
    const noun=picker.dataset.bind==='mcp'?'MCP 工具':'技能';
    const countEl=picker.dataset.bind==='mcp'?$('#bindMcpCount'):$('#bindSkillCount');
    if(countEl) countEl.textContent=boxes.length?`${checked.length?`已选 ${checked.length} 个`:'未选择'} · 共 ${boxes.length} 个`:'未选择';
    const summary=picker.querySelector('.bind-picker-summary');
    if(!summary) return;
    if(!checked.length){
      summary.innerHTML=`<span class="bind-picker-placeholder">点击选择 ${noun}</span>`;
      return;
    }
    summary.innerHTML=checked.map(box=>{
      const title=box.closest('.bind-option')?.querySelector('b')?.textContent||box.value;
      return `<span class="bind-chip">${escapeHtml(title)}<button type="button" class="bind-chip-x" data-unbind="${box.name}" data-id="${box.value}" aria-label="取消 ${escapeHtml(title)}">×</button></span>`;
    }).join('');
  });
}
function roleFormHtml(row){
  const selected=new Set(row&&row.permissions||[]);
  const catalog=(authState.me&&authState.me.catalog)||[];
  const groups={};
  catalog.forEach(item=>{ (groups[item.group]=groups[item.group]||[]).push(item); });
  const boxes=Object.entries(groups).map(([group,items])=>`<div class="perm-group"><small>${escapeHtml(group)}</small>${items.map(item=>`<label class="perm-check"><input type="checkbox" name="permissions" value="${escapeHtml(item.key)}" ${selected.has(item.key)?'checked':''}><span>${escapeHtml(item.key)}</span><em>${escapeHtml(item.label)}</em></label>`).join('')}</div>`).join('');
  return `<div class="field"><label>角色名称</label><input name="name" placeholder="业务观察员" required value="${escapeHtml(row&&row.name||'')}"></div>
    <div class="field"><label>角色说明</label><input name="description" placeholder="只读查看业务数据" value="${escapeHtml(row&&row.description||'')}"></div>
    <div class="field"><label>权限</label><div class="perm-picker">${boxes||'<div class="empty">权限目录未加载</div>'}</div></div>`;
}
function userFormHtml(row, roles){
  const options=(roles||[]).map(role=>`<option value="${role.id}" ${Number(role.id)===Number(row.role_id)?'selected':''}>${escapeHtml(role.name)}</option>`).join('');
  return `<div class="field"><label>用户名</label><input value="${escapeHtml(row.username)}" disabled></div>
    <div class="field"><label>显示名</label><input name="display_name" value="${escapeHtml(row.display_name||row.username)}"></div>
    <div class="field"><label>角色</label><select class="select" style="width:100%" name="role_id">${options}</select></div>
    <div class="field"><label>状态</label><select class="select" style="width:100%" name="enabled"><option value="true" ${row.enabled?'selected':''}>启用</option><option value="false" ${row.enabled?'':'selected'}>停用</option></select></div>
    <div class="field"><label>新密码（留空不改）</label><input type="password" name="password" autocomplete="new-password"></div>`;
}
function openCreate(page){openForm(page)}
function openEdit(page,id){const row=resourceStore[page]&&resourceStore[page][id];if(!row){toast('未找到该配置');return}openForm(page,row)}
async function openUserEdit(id){
  const row=resourceStore.users&&resourceStore.users[id];
  if(!row){toast('未找到该用户');return}
  const roles=Object.values(resourceStore.roles||{});
  $('#modalEyebrow').textContent='权限管理';
  $('#modalTitle').textContent='编辑用户';
  resetModalSubmit('保存修改');
  $('#modal').classList.remove('modal-wide');
  $('#modalFields').innerHTML=userFormHtml(row, roles);
  $('#modalForm').dataset.page='users';
  $('#modalForm').dataset.id=String(row.id);
  $('#modalForm').noValidate=false;
  $('#modal').showModal();
}
async function removeResource(page,id){const row=resourceStore[page]&&resourceStore[page][id];const name=(row&&row.name)||'该配置';if(!confirm(`确定删除「${name}」吗？此操作不可恢复。`))return;try{await api(`/api/${page}/${id}`,{method:'DELETE'});await afterChange(page,'配置已删除')}catch(e){toast('删除失败，请稍后重试')}}
async function openForm(page,row){
  if(!forms[page]){toast('已进入新建向导');return}
  const editing=!!row;
  const names={agents:'新建 Agent',mcp:'添加 MCP 服务',skills:'添加 Skill',models:'添加模型配置',sandboxes:'新建沙箱策略',roles:'新建角色'};
  const edits={agents:'编辑 Agent',mcp:'编辑 MCP 服务',skills:'编辑 Skill',models:'编辑模型配置',sandboxes:'编辑沙箱策略',roles:'编辑角色'};
  $('#modalEyebrow').textContent=editing?'编辑配置':'新建配置';
  $('#modalTitle').textContent=editing?edits[page]:names[page];
  resetModalSubmit(editing?'保存修改':'确认添加');
  $('#modalForm').noValidate=false;
  $('#modal').classList.toggle('modal-wide', page==='agents'||page==='skills'||page==='roles');
  let modelOptions=[], mcpRows=[], skillRows=[];
  if(page==='roles'){
    $('#modalFields').innerHTML=roleFormHtml(row);
  } else if(page==='mcp'){
    $('#modalFields').innerHTML=mcpFormHtml(row);
    bindMcpForm();
  } else if(page==='sandboxes'){
    $('#modalFields').innerHTML=sandboxFormHtml(row);
  } else if(page==='agents'){
    const extras=await Promise.all([api('/api/models'), api('/api/mcp'), api('/api/skills'), api('/api/sandboxes')]);
    modelOptions=extras[0].filter(model=>model.enabled||model.name===row?.model_name);
    mcpRows=extras[1]; skillRows=extras[2];
    $('#modalFields').innerHTML=agentFormHtml(row, modelOptions, mcpRows, skillRows, extras[3]);
    updateBindCounts();
  } else {
    $('#modalFields').innerHTML=forms[page].map(f=>{
      const control=f[0]==='model_name'
        ? `<select class="select" style="width:100%" name="${f[0]}" required>${modelOptions.map(m=>`<option value="${m.name}">${m.name} · ${m.model_id}</option>`).join('')}</select>`
        : f[0]==='instruction'
        ? `<textarea name="instruction" class="skill-md" placeholder="${f[2]}" ${editing?'':'required'}></textarea>`
        : f[0]==='api_key'
        ? `<input type="password" name="api_key" placeholder="${f[2]}" autocomplete="new-password">`
        : `<input name="${f[0]}" placeholder="${f[2]}" ${['name','transport','endpoint','provider','model_id'].includes(f[0])?'required':''}>`;
      return `<div class="field"><label>${f[1]}</label>${control}</div>`;
    }).join('');
    if(row){
      forms[page].forEach(([key,,hint])=>{
        const el=$(`#modalForm [name="${key}"]`);
        if(!el) return;
        if(key==='api_key'){el.placeholder=row.has_api_key?'已保存密钥，留空则不修改':hint;el.value='';return}
        let val=row[key];
        if(key==='permissions'&&Array.isArray(val)) val=val.join(', ');
        el.value=val??'';
      });
    }
  }
  $('#modalForm').dataset.page=page;
  $('#modalForm').dataset.id=editing?String(row.id):'';
  $('#modal').showModal();
}
$('#modalForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const form=e.currentTarget,page=form.dataset.page,id=form.dataset.id,data=Object.fromEntries(new FormData(form));
  if(page==='preview'){ $('#modal').close(); return; }
  if(page==='eval-dataset'){
    try{await evalSubmitDataset(form)}catch(err){toast(apiError(err)||'创建数据集失败')}
    return;
  }
  if(page==='eval-case'){
    try{await evalSubmitCase(form)}catch(err){toast(apiError(err)||'保存用例失败')}
    return;
  }
  if(page==='evaluations'){
    try{await evalLaunchFromForm(form)}catch(err){toast(apiError(err)||'创建测试失败，请检查 Agent 与数据集')}
    return;
  }
  if(page==='agents'){
    data.skill_ids=[...form.querySelectorAll('input[name="skill_ids"]:checked')].map(x=>Number(x.value));
    data.mcp_ids=[...form.querySelectorAll('input[name="mcp_ids"]:checked')].map(x=>Number(x.value));
    data.sandbox_id=data.sandbox_id?Number(data.sandbox_id):null;
  }
  if(page==='sandboxes'&&data.timeout_seconds!==undefined)data.timeout_seconds=Number(data.timeout_seconds);
  if(page==='mcp'){
    const auth=String(data.auth||'').trim();
    delete data.auth;
    if(auth){
      const headers={};
      if(auth.includes(':')&&!/^bearer\s+/i.test(auth)){
        const idx=auth.indexOf(':');
        headers[auth.slice(0,idx).trim()]=auth.slice(idx+1).trim();
      }else{
        headers.Authorization=/^bearer\s+/i.test(auth)?auth:'Bearer '+auth;
      }
      data.config={headers};
    }
  }
  if(page==='roles'){
    data.permissions=[...form.querySelectorAll('input[name="permissions"]:checked')].map(x=>x.value);
  }
  if(page==='users'){
    data.role_id=Number(data.role_id);
    data.enabled=data.enabled==='true';
    if(!data.password) delete data.password;
  }
  if(!id)Object.keys(data).forEach(key=>{if(data[key]==='')delete data[key]});
  if(data.api_key==='')delete data.api_key;
  if(data.temperature!==undefined)data.temperature=Number(data.temperature);
  if(data.timeout_seconds!==undefined)data.timeout_seconds=Number(data.timeout_seconds);
  if(data.permissions!==undefined && !Array.isArray(data.permissions)) data.permissions=String(data.permissions).split(',').map(x=>x.trim()).filter(Boolean);
  if(page==='mcp'&&!id&&data.config===undefined)data.config={};
  try{
    await api(id?`/api/${page}/${id}`:`/api/${page}`,{method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    $('#modal').close();
    await afterChange(page==='users'?'roles':page, id?'配置已更新':'配置已添加');
  }catch(err){toast(id?'更新失败，请检查输入内容':'保存失败，请检查必填项')}
});
document.querySelectorAll('[data-close-modal]').forEach(button=>button.addEventListener('click',()=>$('#modal').close()));
function closeBindPickers(except){
  document.querySelectorAll('#modalForm .bind-picker.open').forEach(picker=>{
    if(picker===except) return;
    picker.classList.remove('open');
    const toggle=picker.querySelector('.bind-picker-toggle');
    if(toggle) toggle.setAttribute('aria-expanded','false');
  });
}
$('#modalForm').addEventListener('change', e=>{
  if(e.target && (e.target.name==='mcp_ids' || e.target.name==='skill_ids')) updateBindCounts();
});
$('#modalForm').addEventListener('click', e=>{
  const chip=e.target.closest('.bind-chip-x');
  if(chip){
    e.preventDefault();
    e.stopPropagation();
    const box=$('#modalForm').querySelector(`input[name="${chip.dataset.unbind}"][value="${chip.dataset.id}"]`);
    if(box){box.checked=false;box.dispatchEvent(new Event('change',{bubbles:true}));}
    return;
  }
  const toggle=e.target.closest('.bind-picker-toggle');
  if(toggle){
    e.preventDefault();
    const picker=toggle.closest('.bind-picker');
    const open=!picker.classList.contains('open');
    closeBindPickers(open?picker:null);
    picker.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
    if(open){
      const search=picker.querySelector('.bind-picker-search');
      if(search) search.focus();
    }
  }
});
$('#modalForm').addEventListener('input', e=>{
  if(!e.target.classList.contains('bind-picker-search')) return;
  const q=e.target.value.trim().toLowerCase();
  e.target.closest('.bind-picker').querySelectorAll('.bind-option').forEach(opt=>{
    opt.hidden=!!q && !(opt.dataset.search||'').includes(q);
  });
});
$('#modal').addEventListener('click', e=>{
  if(!e.target.closest('.bind-picker')) closeBindPickers();
  const jump=e.target.closest('[data-jump]');
  if(!jump) return;
  e.preventDefault();
  const page=jump.dataset.jump;
  const id=jump.dataset.id;
  $('#modal').close();
  render(page).then(()=>{ if(id) openEdit(page, Number(id)); });
});
const sessionModal=$('#sessionModal');
if(sessionModal){
  sessionModal.addEventListener('click', e=>{ if(e.target===sessionModal) closeSessionDetail(); });
  sessionModal.addEventListener('cancel', e=>{ e.preventDefault(); closeSessionDetail(); });
}
async function testModel(id){try{const r=await api(`/api/models/${id}/test`,{method:'POST'});toast(r.message)}catch(e){toast('模型配置检查失败')}}
async function testMcp(id){try{const r=await api(`/api/mcp/${id}/test`,{method:'POST'});toast(r.message)}catch(e){toast('MCP 探测失败')}}
function openSkillPreview(id, data){
  const row=resourceStore.skills&&resourceStore.skills[id];
  const name=(row&&row.name)||'Skill';
  $('#modalEyebrow').textContent='SKILL PREVIEW';
  $('#modalTitle').textContent=name+' · 指令预览';
  $('#modalSubmit').hidden=true;
  $('#modal').classList.add('modal-wide');
  const body=data.instruction?escapeHtml(data.instruction):'该 Skill 没有可执行指令正文。';
  $('#modalFields').innerHTML=`<p class="skill-preview-meta">${escapeHtml(data.message||'')}</p><pre class="skill-preview">${body}</pre>`;
  $('#modalForm').dataset.page='preview';
  $('#modalForm').dataset.id='';
  $('#modal').showModal();
}
async function testSkill(id){
  try{
    const r=await api(`/api/skills/${id}/test`,{method:'POST'});
    openSkillPreview(id, r);
  }catch(e){toast((e&&e.message)||'Skill 预览失败')}
}
function openSandboxProbe(id, data){
  const row=resourceStore.sandboxes&&resourceStore.sandboxes[id];
  const name=(row&&row.name)||'沙箱';
  $('#modalEyebrow').textContent='SANDBOX RUN';
  $('#modalTitle').textContent=name+' · 试跑结果';
  $('#modalSubmit').hidden=true;
  $('#modal').classList.add('modal-wide');
  const output=data.sample||data.error||'没有输出';
  $('#modalFields').innerHTML=`<p class="skill-preview-meta">${escapeHtml(data.message||'')}</p><pre class="skill-preview">${escapeHtml(output)}</pre>`;
  $('#modalForm').dataset.page='preview';
  $('#modalForm').dataset.id='';
  $('#modal').showModal();
}
async function testSandbox(id){
  try{
    const r=await api(`/api/sandboxes/${id}/test`,{method:'POST'});
    openSandboxProbe(id, r);
    if(r.ready) toast(r.message);
  }catch(e){toast((e&&e.message)||'沙箱试跑失败')}
}
async function toggleEnabled(page,id,enabled){try{await api(`/api/${page}/${id}/status`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})});await afterChange(page, enabled?'已启用':'已停用')}catch(e){toast('状态修改失败，请稍后重试')}}
function openPlayground(agentId){chatState.agentId=String(agentId);render('playground',agentId)}
function escapeHtml(value){const el=document.createElement('div');el.textContent=String(value??'');return el.innerHTML}
const chatState={sessionId:'',messages:[],agentId:'',modelId:'',spans:[],traceId:'',latencyMs:0,mode:'',workspace:''};
function chatKey(agentId){return 'pg_chat_'+(agentId||chatState.agentId||'default')}
function persistChat(){if(!chatState.agentId)return;sessionStorage.setItem(chatKey(chatState.agentId),JSON.stringify(chatState))}
function loadChat(agentId){try{const saved=JSON.parse(sessionStorage.getItem(chatKey(agentId||chatState.agentId))||'{}');if(saved&&typeof saved==='object')Object.assign(chatState,{sessionId:saved.sessionId||'',messages:Array.isArray(saved.messages)?saved.messages:[],agentId:String(agentId||saved.agentId||chatState.agentId||''),modelId:saved.modelId||chatState.modelId,spans:Array.isArray(saved.spans)?saved.spans:[],traceId:saved.traceId||'',latencyMs:saved.latencyMs||0,mode:saved.mode||'',workspace:saved.workspace||''})}catch(e){}}
async function restoreAgentChat(agentId){
  chatState.agentId=String(agentId||'');
  loadChat(chatState.agentId);
  if(!chatState.agentId) return;
  try{
    const ws=await api('/api/agents/'+chatState.agentId+'/workspace');
    chatState.workspace=ws.path||'';
    const latest=(ws.sessions||[])[0];
    if(!chatState.sessionId && latest){
      chatState.sessionId=latest.session_id||'';
      chatState.messages=(latest.messages||[]).map(item=>({role:item.role,content:item.content,agent:item.agent_name,error:!!item.error}));
      const last=(latest.traces||[]).slice(-1)[0];
      chatState.spans=last&&last.spans||[];
      chatState.traceId=last&&last.trace_id||'';
    }
    persistChat();
  }catch(e){}
}
function currentAgentName(){const el=$('#runAgent');return el&&el.selectedOptions[0]?el.selectedOptions[0].textContent:'Agent'}
function currentAgent(){
  const select=$('#runAgent');
  const id=chatState.agentId || (select && select.value);
  return pgCatalog.agents.find(x=>String(x.id)===String(id));
}
function paintChat(){
  const log=$('#chatLog');if(!log)return;
  if(!chatState.messages.length){log.innerHTML=`<div class="wechat-empty"><div class="result-mark">${escapeHtml((currentAgentName()||'A')[0])}</div><b>发一条消息开始对话</b><p>回复会显示在左侧；右侧同步展示这次调用的执行链路。</p></div>`;return}
  log.innerHTML=chatState.messages.map(msg=>{
    const mine=msg.role==='user';
    const name=mine?'我':escapeHtml(msg.agent||currentAgentName());
    const avatar=mine?'林':name[0];
    return `<div class="wx-row ${mine?'mine':'theirs'}"><i class="wechat-avatar ${mine?'me':'agent'}">${avatar}</i><div class="wx-col"><span class="wx-name">${name}</span><div class="wx-bubble ${msg.error?'error':''}">${escapeHtml(msg.content).replace(/\n/g,'<br>')}</div></div></div>`;
  }).join('');
  log.scrollTop=log.scrollHeight;
}
function paintTrace(){
  const box=$('#traceList'), meta=$('#traceMeta'), path=$('#workspacePath');
  if(!box) return;
  const spans=chatState.spans||[];
  if(meta){
    if(!spans.length) meta.textContent='发送消息后，这里会按步骤写入当前 Agent 工作空间';
    else meta.textContent=[chatState.traceId, chatState.latencyMs?chatState.latencyMs+' ms':'', chatState.mode==='preview'?'预览':(chatState.mode==='error'?'失败':'完成')].filter(Boolean).join(' · ');
  }
  if(path) path.textContent=chatState.workspace||currentAgent()&&currentAgent().workspace||'';
  if(!spans.length){
    box.innerHTML='<div class="trace-empty"><b>还没有链路</b><p>发送一条消息后，会话、回复和执行步骤会保存在这个 Agent 的工作空间里。</p></div>';
    return;
  }
  box.innerHTML=spans.map(span=>`<div class="trace-step ${span.status||'ok'}">
    <i class="trace-dot"></i>
    <div class="trace-card">
      <div class="trace-card-top"><b>${escapeHtml(span.title||span.name)}</b>${span.duration_ms?`<span>${span.duration_ms} ms</span>`:''}</div>
      ${span.detail?`<p>${escapeHtml(span.detail)}</p>`:''}
    </div>
  </div>`).join('');
}
function syncBindHint(){
  const el=$('#pgBindHint'); if(!el) return;
  const agent=currentAgent();
  const skills=(pgCatalog.skills||[]).filter(x=>(agent&&(agent.skill_ids||[]).map(Number)||[]).includes(x.id));
  const mcps=(pgCatalog.mcps||[]).filter(x=>(agent&&(agent.mcp_ids||[]).map(Number)||[]).includes(x.id));
  const tools=mcps.flatMap(x=>(x.tools||[]).map(t=>t.name));
  const skillText=skills.length?skills.map(x=>x.name).join('、'):'未关联技能';
  const toolText=tools.length?tools.join('、'):'未关联工具';
  el.innerHTML=`<span>${escapeHtml(skillText)} · ${escapeHtml(toolText)}</span>${agent?`<button type="button" class="bind-link" id="pgEditAgent">去配置</button>`:''}`;
  const btn=$('#pgEditAgent');
  if(btn) btn.onclick=()=>{resourceStore.agents=Object.fromEntries((pgCatalog.agents||[]).map(x=>[x.id,x]));openEdit('agents', agent.id)};
}
function syncChatHeader(){
  const agent=$('#runAgent'),model=$('#runModel');
  if(agent&&agent.selectedOptions[0]){$('#chatPeerName').textContent=agent.selectedOptions[0].textContent;$('#chatAvatar').textContent=agent.selectedOptions[0].textContent[0]}
  if(model&&model.selectedOptions[0]&&$('#chatPeerMeta'))$('#chatPeerMeta').textContent=model.selectedOptions[0].textContent;
  syncBindHint();
}
function resetChat(){chatState.sessionId='';chatState.messages=[];chatState.spans=[];chatState.traceId='';chatState.latencyMs=0;chatState.mode='';persistChat();paintChat();paintTrace();const state=$('#runState');if(state){state.className='pill draft';state.textContent='待发送'}}
async function runPlayground(){
  const button=$('#runButton'),state=$('#runState'),input=$('#runMessage'),log=$('#chatLog');
  if(!button||!input||!log)return;
  const message=input.value.trim();
  if(!message){toast('请输入消息');return}
  if(!$('#runModel').value){toast('请先启用一个模型');return}
  chatState.agentId=$('#runAgent').value;chatState.modelId=$('#runModel').value;
  chatState.messages.push({role:'user',content:message,agent:'我'});
  persistChat();input.value='';input.style.height='auto';paintChat();
  log.insertAdjacentHTML('beforeend',`<div class="wx-row theirs" id="chatTyping"><i class="wechat-avatar agent">${escapeHtml(currentAgentName()[0])}</i><div class="wx-col"><span class="wx-name">${escapeHtml(currentAgentName())}</span><div class="wx-bubble typing"><i></i><i></i><i></i></div></div></div>`);
  log.scrollTop=log.scrollHeight;
  button.disabled=true;if(state){state.className='pill running';state.textContent='回复中'}
  chatState.spans=[{title:'接收用户消息',kind:'input',status:'ok'},{title:'调用模型',kind:'llm',status:'ok',detail:'正在生成回复…'}];
  paintTrace();
  try{
    const r=await api('/api/playground/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:Number($('#runAgent').value),model_config_id:Number($('#runModel').value),message,session_id:chatState.sessionId||undefined})});
    const reply=r.reply||r.output||r.response||'没有返回内容';
    chatState.sessionId=r.session_id||chatState.sessionId;
    chatState.traceId=r.trace_id||'';
    chatState.latencyMs=r.latency_ms||0;
    chatState.mode=r.mode||'';
    chatState.spans=Array.isArray(r.spans)?r.spans:[];
    chatState.workspace=r.workspace||chatState.workspace;
    chatState.messages.push({role:'assistant',content:reply,agent:r.agent||currentAgentName(),error:r.mode==='error'});
    persistChat();
    if(state){state.className=r.mode==='error'?'pill failed':r.mode==='ready'?'pill completed':'pill queued';state.textContent=r.mode==='ready'?'已回复':r.mode==='error'?'调用失败':'预览回复'}
    paintChat();paintTrace();
  }catch(e){
    chatState.messages.push({role:'assistant',content:e.message||'没有获得返回，请检查 Agent 与模型配置',agent:currentAgentName(),error:true});
    chatState.spans=[{title:'调用失败',kind:'output',status:'error',detail:e.message||'请求未成功'}];
    persistChat();if(state){state.className='pill failed';state.textContent='发送失败'}paintChat();paintTrace();
  }finally{button.disabled=false;input.focus()}
}
function bindPage(page){
  evalStopPoll();
  if(page==='evaluations') bindEvalPage();
  if(page==='sessions'){const runFilter=async()=>{const p=new URLSearchParams();const q=$('#sessionQ').value.trim(),a=$('#agentFilter').value,s=$('#statusFilter').value;if(q)p.set('q',q);if(a)p.set('agent_name',a);if(s)p.set('status',s);const rows=await api('/api/sessions?'+p);$('#sessionResults').innerHTML=sessionTable(rows).replace('<section class="panel wide-panel">','<section>');closeSessionDetail()};$('#doFilter').onclick=runFilter;$('#sessionQ').onkeydown=e=>{if(e.key==='Enter')runFilter()};$('#sessionResults').onclick=e=>{const hit=e.target.closest('[data-session-id]');if(hit)openSessionDetail(hit.dataset.sessionId)}}if(page==='playground'){paintChat();paintTrace();syncChatHeader();const form=$('#chatForm'),input=$('#runMessage');if(form)form.onsubmit=e=>{e.preventDefault();runPlayground()};if(input){input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();runPlayground()}});input.addEventListener('input',()=>{input.style.height='auto';input.style.height=Math.min(120,input.scrollHeight)+'px'})}const agentSel=$('#runAgent'),modelSel=$('#runModel');if(agentSel)agentSel.onchange=async()=>{if(String(chatState.agentId)!==agentSel.value){await restoreAgentChat(agentSel.value);paintChat();paintTrace()}syncChatHeader()};if(modelSel)modelSel.onchange=()=>{chatState.modelId=modelSel.value;persistChat();syncChatHeader()};if($('#clearChat'))$('#clearChat').onclick=resetChat;input&&input.focus()}if(page==='workflows') bindWorkflowCanvas()}
let renderSeq=0;
async function render(page,param=''){
  if(page==='traces')page='studio';
  currentPage=page;
  currentParam=param;
  const seq=++renderSeq;
  $('#crumbTitle').textContent='/ '+(titles[page]||page);
  $('#content').innerHTML='<div class="loading">正在载入数据…</div>';
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active',n.dataset.page===page));
  try{
    let html;
    if(page==='dashboard')html=await dashboard();
    else if(page==='sessions')html=await sessions();
    else if(page==='studio')html=await studio();
    else if(page==='evaluations')html=await evaluations();
    else if(page==='playground')html=await playground(param);
    else if(page==='roles')html=await iam();
    else html=await resources(page);
    if(seq!==renderSeq) return;
    $('#content').innerHTML=html;
    bindPage(page);
  }catch(e){
    if(seq!==renderSeq) return;
    $('#content').innerHTML='<div class="empty">数据加载失败，请确认后端服务已启动。</div>';
  }
}
document.querySelectorAll('.nav-item').forEach(n=>n.onclick=()=>{render(n.dataset.page);$('.sidebar').classList.remove('open')});
$('#menuBtn').onclick=()=>$('.sidebar').classList.toggle('open');
if($('#logoutBtn')) $('#logoutBtn').onclick=logout;
if($('#logoutTopBtn')) $('#logoutTopBtn').onclick=logout;
if($('#tenantSwitch')) $('#tenantSwitch').onchange=async e=>{localStorage.setItem('af_tenant_id', e.target.value); const me=await api('/api/auth/me'); applyMe(me); render('dashboard')};
if($('#loginForm')) $('#loginForm').onsubmit=async e=>{
  e.preventDefault();
  try{
    const body={username:$('#loginUser').value.trim(),password:$('#loginPass').value};
    const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok) throw new Error('用户名或密码错误');
    const data=await r.json();
    authState.token=data.token;
    localStorage.setItem('af_token', data.token);
    localStorage.removeItem('af_tenant_id');
    hideLogin();
    applyMe(await api('/api/auth/me'));
    render('dashboard');
  }catch(err){showLogin(err.message||'登录失败')}
};
async function boot(){
  if(!authState.token){showLogin();return}
  try{
    applyMe(await api('/api/auth/me'));
    hideLogin();
    const first=[...document.querySelectorAll('.nav-item')].find(btn=>!btn.hidden);
    render(first?first.dataset.page:'dashboard');
  }catch(e){showLogin('请重新登录')}
}
boot();
