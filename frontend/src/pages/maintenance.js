import {maintenance,jobs} from '../api/entities.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,fmt,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {PlanStatus,UnscheduledReasonText} from '../constants/enums.js';

const isAdmin=()=>session.user.role==='admin';
const canWrite=()=>session.user.role!=='viewer';
// 确认键本地持久：回执丢失重试沿用同一安排，绝不重复占用资源
const keyStore={
 get(planId){const raw=JSON.parse(localStorage.getItem('confirm-keys')||'{}');return raw[planId]||null;},
 put(planId,key){const raw=JSON.parse(localStorage.getItem('confirm-keys')||'{}');raw[planId]=key;localStorage.setItem('confirm-keys',JSON.stringify(raw));},
};
const newKey=planId=>'cf-'+planId+'-'+Date.now().toString(36)+Math.random().toString(36).slice(2,8);
const dtLocal=t=>new Date(t*1000);
const toInput=t=>{const d=dtLocal(t),p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+'T'+p(d.getHours())+':'+p(d.getMinutes());};
const fromInput=v=>Math.floor(new Date(v).getTime()/1000);

export async function maintenancePage(){
 const [crews,windows,tasks,plans,allJobs]=await Promise.all([
  maintenance.crews.list(),maintenance.windows.list(),maintenance.tasks.list(),maintenance.plans.list(),jobs.list()]);
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed');
 const now=Date.now()/1000;
 return heading('06 / MAINTENANCE','封锁检修排程','从已批准试验的缺陷带登记检修任务，按班组与天窗试排；确认整组占用资源，冲突全组不生效。',
  '<button class="button" id="refresh-maint">刷新排程 ↻</button>')
 +'<div class="maint-stack">'

 // ---------- 资源维护 ----------
 +'<section class="panel"><div class="section-title"><h2>检修资源：班组与封锁天窗</h2><span>班组 '+crews.length+' · 天窗 '+windows.length+'</span></div>'
 +'<div class="report-grid"><form id="crew-form"><h3>新增班组</h3><label>班组名称<input name="name" required minlength="2" placeholder="例如：南环一工班"></label><label>擅长作业<input name="skill" placeholder="例如：换轨 / 捣固 / 焊缝"></label><button class="primary" '+(isAdmin()?'':'disabled')+'>保存班组</button></form>'
 +'<form id="window-form"><h3>新增封锁天窗</h3><label>天窗名称<input name="name" required minlength="2" placeholder="例如：11 月 20 日夜间窗"></label><div class="parameter-grid"><label>开始<input type="datetime-local" name="start_at" required value="'+toInput(now+86400)+'"></label><label>结束<input type="datetime-local" name="end_at" required value="'+toInput(now+86400+6*3600)+'"></label></div><label>线路方向<input name="line_direction" value="南环上行"></label><button class="primary" '+(isAdmin()?'':'disabled')+'>保存天窗</button></form></div>'
 +'<div class="table-wrap"><table><thead><tr><th>类型</th><th>名称</th><th>时段 / 擅长</th><th>版本</th><th>状态</th><th>操作</th></tr></thead><tbody>'
 +crews.map(c=>'<tr><td>班组</td><td>'+esc(c.name)+'</td><td>'+esc(c.skill||'—')+'</td><td>v'+c.version+'</td><td>'+(c.active?'可用':'停用')+'</td><td>'+(isAdmin()?'<button class="button small" data-crew-toggle="'+c.id+'" data-active="'+c.active+'">'+(c.active?'停用':'启用')+'</button>':'—')+'</td></tr>').join('')
 +windows.map(w=>'<tr><td>天窗</td><td>'+esc(w.name)+'</td><td>'+date(w.start_at)+' → '+date(w.end_at)+'</td><td>v'+w.version+'</td><td>'+(w.active?'可用':'已撤销')+'</td><td>'+(isAdmin()?'<button class="button small" data-window-shorten="'+w.id+'">缩短2小时</button> <button class="button small" data-window-toggle="'+w.id+'" data-active="'+w.active+'">'+(w.active?'撤销':'恢复')+'</button>':'—')+'</td></tr>').join('')
 +'</tbody></table></div><p class="hint">天窗缩短或撤销后，落在该窗上的已确认方案立即失效并拒绝开工，其他已确认安排保留；资源版本递增，旧试排不能再确认。</p></section>'

 // ---------- 任务登记 ----------
 +'<section class="panel"><div class="section-title"><h2>缺陷带检修任务（登记每带工时）</h2><span>'+tasks.length+' 条</span></div>'
 +'<form id="task-gen-form" class="inline-form"><label>已批准试验<select name="job_id" required>'+approved.map(j=>'<option value="'+j.id+'">'+esc(j.name)+'</option>').join('')+'</select></label><label>默认工时(小时)<input type="number" name="default_hours" value="2" min="0.5" max="24" step="0.5"></label><button class="primary" '+(canWrite()&&approved.length?'':'disabled')+'>从缺陷带登记任务</button></form>'
 +'<p class="hint">仅登记 DBSCAN 识别出的密集缺陷带，孤立/稀疏点不登记；同一试验重复登记自动跳过。</p>'
 +'<div class="table-wrap"><table><thead><tr><th>任务</th><th>里程 km</th><th>工时(h)</th><th>备注</th><th>状态</th><th>调整工时</th></tr></thead><tbody>'
 +tasks.map(t=>'<tr><td>'+esc(t.title)+'</td><td>'+fmt(t.from_km)+' ~ '+fmt(t.to_km)+'</td><td>'+fmt(t.work_hours)+'</td><td>'+esc(t.note||'—')+'</td><td>'+(t.active?'在排':'停用')+'</td><td>'+(canWrite()?'<input type="number" class="hours-input" data-task="'+t.id+'" value="'+t.work_hours+'" min="0.5" max="24" step="0.5"> <button class="button small" data-task-hours="'+t.id+'">保存</button>':'—')+'</td></tr>').join('')
 +'</tbody></table></div></section>'

 // ---------- 试排 ----------
 +'<section class="panel"><div class="section-title"><h2>试排方案（不立即占用资源）</h2></div>'
 +'<form id="trial-form" class="inline-form"><label>方案名称<input name="name" required minlength="2" value="封锁检修试排 '+new Date().toLocaleDateString('zh-CN')+'"></label><label>范围<select name="scope"><option value="all">全部在排任务</option></select></label><button class="primary" '+(canWrite()&&tasks.length?'':'disabled')+'>开始试排</button></form>'
 +'<p class="hint">同一班组时间重叠、里程区间时间重叠均不能同时开工；作业完整落在单个天窗内，跨天窗不拆工。未排入的任务会给出原因。</p></section>'

 // ---------- 方案列表 ----------
 +'<section class="panel"><div class="section-title"><h2>检修方案与确认</h2><span>'+plans.length+' 个方案</span></div>'
 +plans.map(p=>renderPlan(p,crews,windows,tasks)).join('')
 +(!plans.length?'<div class="empty">还没有试排方案。先维护班组与天窗，再从上方发起试排。</div>':'')
 +'</section></div>';
}

function renderPlan(p,crews,windows,tasks){
 const crewName=id=>(crews.find(c=>c.id===id)||{name:'班组#'+id}).name;
 const winName=id=>(windows.find(w=>w.id===id)||{name:'天窗#'+id}).name;
 const taskTitle=id=>(tasks.find(t=>t.id===id)||{title:'任务#'+id}).title;
 const storedKey=keyStore.get(p.id);
 const statusBadge='<span class="status plan-status '+p.status+'">'+(PlanStatus[p.status]||p.status)+'</span>';
 const items=p.items.length?'<div class="table-wrap"><table><thead><tr><th>检修任务</th><th>班组</th><th>天窗</th><th>开工</th><th>完工</th><th>工时</th></tr></thead><tbody>'
  +p.items.map(i=>'<tr><td>'+esc(taskTitle(i.task_id))+'</td><td>'+esc(crewName(i.crew_id))+'</td><td>'+esc(winName(i.window_id))+'</td><td>'+date(i.start_at)+'</td><td>'+date(i.end_at)+'</td><td>'+fmt(i.work_hours)+'h</td></tr>').join('')
  +'</tbody></table></div>':'<div class="empty">本方案没有可确认安排，请查看未排原因后调整资源再试排。</div>';
 const reasons=p.unscheduled.length?'<div class="reasons"><h3>未排原因（'+p.unscheduled.length+' 条）</h3>'+p.unscheduled.map(r=>'<div class="reason-row"><strong>'+esc(taskTitle(r.task_id))+'</strong><span class="reason-code">'+esc(UnscheduledReasonText[r.code]||r.code)+'</span><p>'+esc(r.detail)+'</p></div>').join('')+'</div>':'';
 const affected=p.status==='invalidated'?'<div class="warning">受影响安排：'+esc(p.invalidate_reason||'资源已变更')+'（失效时间 '+date(p.invalidated_at)+'）</div>':'';
 let actions='';
 if(p.status==='draft'){
  actions='<div class="plan-actions"><button class="primary" data-confirm="'+p.id+'" '+(isAdmin()&&p.items.length?'':'disabled')+'>确认并整组占用</button>'
   +(storedKey?'<button class="button small" data-retry="'+p.id+'">用原确认键重试</button>':'')
   +'<small class="hint">'+(storedKey?'已保存确认键，回执丢失可安全重试':'确认键将在首次确认时生成并本地保存')+'</small></div>';
 }else if(p.status==='confirmed'){
  actions='<div class="plan-actions"><button class="primary" data-start="'+p.id+'" '+ (isAdmin()?'':'disabled')+'>申请开工</button><small class="hint">确认于 '+date(p.confirmed_at)+'</small></div>';
 }
 return '<article class="plan-card '+p.status+'"><div class="section-title"><h2>#'+p.id+' '+esc(p.name)+'</h2><span>'+statusBadge+' · '+p.items.length+' 条安排 · '+p.unscheduled.length+' 条未排</span></div>'+affected+items+reasons+actions+'</article>';
}

export function bindMaintenance(refresh){
 document.querySelector('#refresh-maint').onclick=refresh;
 submit(document.querySelector('#crew-form'),async f=>{await maintenance.crews.create({name:f.get('name'),skill:f.get('skill')});notify('班组已保存');await refresh();});
 submit(document.querySelector('#window-form'),async f=>{await maintenance.windows.create({name:f.get('name'),start_at:fromInput(f.get('start_at')),end_at:fromInput(f.get('end_at')),line_direction:f.get('line_direction')});notify('天窗已保存');await refresh();});
 submit(document.querySelector('#task-gen-form'),async f=>{const r=await maintenance.tasks.generate({job_id:Number(f.get('job_id')),default_hours:Number(f.get('default_hours'))});notify('登记 '+r.created.length+' 条任务，跳过已登记 '+r.skipped_band_index.length+' 条');await refresh();});
 submit(document.querySelector('#trial-form'),async f=>{await maintenance.plans.trial({name:f.get('name')});notify('试排完成，请查看安排与未排原因');await refresh();});

 document.querySelectorAll('[data-task-hours]').forEach(b=>b.onclick=async()=>{
  const input=document.querySelector('.hours-input[data-task="'+b.dataset.taskHours+'"]');
  try{await maintenance.tasks.patch(Number(b.dataset.taskHours),{work_hours:Number(input.value)});notify('工时已更新，需重新试排生效');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-crew-toggle]').forEach(b=>b.onclick=async()=>{
  try{await maintenance.crews.patch(Number(b.dataset.crewToggle),{active:b.dataset.active!=='true'});await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-window-toggle]').forEach(b=>b.onclick=async()=>{
  try{const r=await maintenance.windows.patch(Number(b.dataset.windowToggle),{active:b.dataset.active!=='true'});if(r.affected_plans.length)notify('天窗撤销，'+r.affected_plans.length+' 个方案已失效',true);await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-window-shorten]').forEach(b=>b.onclick=async()=>{
  const w=(await maintenance.windows.list()).find(x=>x.id===Number(b.dataset.windowShorten));
  try{const r=await maintenance.windows.patch(w.id,{end_at:w.end_at-2*3600});notify(r.affected_plans.length?'天窗缩短，受影响方案已失效':'天窗已缩短，暂无方案受影响',r.affected_plans.length>0);await refresh();}catch(e){notify(e.message,true);}});

 const doConfirm=async(planId,retry)=>{
  let key=keyStore.get(planId);
  if(!key){key=newKey(planId);keyStore.put(planId,key);}
  await maintenance.plans.confirm(planId,key);
  notify(retry?'已按原确认键重试，未重复占用资源':'方案已确认，资源整组占用');
  await refresh();
 };
 document.querySelectorAll('[data-confirm]').forEach(b=>b.onclick=()=>doConfirm(Number(b.dataset.confirm),false).catch(e=>notify(e.message,true)));
 document.querySelectorAll('[data-retry]').forEach(b=>b.onclick=()=>doConfirm(Number(b.dataset.retry),true).catch(e=>notify(e.message,true)));
 document.querySelectorAll('[data-start]').forEach(b=>b.onclick=async()=>{
  try{await maintenance.plans.start(Number(b.dataset.start));notify('已允许开工');await refresh();}catch(e){notify(e.message,true);}});
}
