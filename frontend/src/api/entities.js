import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const maintenance={
 crews:{list:()=>api('/maintenance/crews'),create:data=>api('/maintenance/crews',{method:'POST',body:JSON.stringify(data)}),patch:(id,data)=>api('/maintenance/crews/'+id,{method:'PATCH',body:JSON.stringify(data)})},
 windows:{list:()=>api('/maintenance/windows'),create:data=>api('/maintenance/windows',{method:'POST',body:JSON.stringify(data)}),patch:(id,data)=>api('/maintenance/windows/'+id,{method:'PATCH',body:JSON.stringify(data)})},
 tasks:{list:()=>api('/maintenance/tasks'),generate:data=>api('/maintenance/tasks/generate',{method:'POST',body:JSON.stringify(data)}),patch:(id,data)=>api('/maintenance/tasks/'+id,{method:'PATCH',body:JSON.stringify(data)})},
 plans:{list:()=>api('/maintenance/plans'),get:id=>api('/maintenance/plans/'+id),trial:data=>api('/maintenance/plans/trial',{method:'POST',body:JSON.stringify(data)}),confirm:(id,key)=>api('/maintenance/plans/'+id+'/confirm',{method:'POST',body:JSON.stringify({confirm_key:key})}),start:id=>api('/maintenance/plans/'+id+'/start',{method:'POST'})},
};
