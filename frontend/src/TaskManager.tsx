import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, ChevronLeft, ChevronRight, Download, FileSearch, FileText, Layers3, ListTodo, Pause, Play, Plus, RefreshCw, Search, Square, Wrench } from "lucide-react";
import { Busy, Empty, Notice, Progress, dateLabel, messageOf } from "./ui";
import { send, studio } from "./studio";
import "./task-manager.css";

export type TaskNavigation = { section: string; action?: string; id?: string; document_id?: string; version_id?: string; prepared_id?: string; operation?: string; run_id?: string; chunk_id?: string; stage?: string; job_id?: string; mode?: string; profile_id?: string };
export type ManagedTask = { id: string; kind: string; title: string; status: string; stage: string; progress: number | null; completed?: number; total?: number; created_at: string; updated_at: string; document_id?: string; version_id?: string; profile_id?: string; model?: string; mode?: string; operation?: string; error?: string; in_flight?: boolean; capabilities: {pause: boolean; resume: boolean; terminate: boolean}; navigation: TaskNavigation };
type TaskPage = {items: ManagedTask[]; total: number; offset: number; limit: number; counts: Record<string,number>; kinds: Record<string,number>};
const kinds: Record<string,string> = {extraction:"作品解析",embedding_download:"模型下载",research:"作品信息",volume_research:"分卷检索",ingestion:"正文上传",conversion:"格式转换",repair:"快捷修复",prompt_workbench:"提示词任务"};
const icons = {extraction:FileSearch,embedding_download:Download,research:BookOpen,volume_research:Layers3,ingestion:FileText,conversion:FileText,repair:Wrench,prompt_workbench:ListTodo};
const labels: Record<string,string> = {queued:"等待开始",prepared:"待确认提示词",sending:"模型回复中",running:"进行中",verifying:"正在校验",pausing:"正在暂停",terminating:"正在终止",cancelling:"正在停止",needs_review:"待确认",awaiting_reply:"等待回复",paused:"已暂停",interrupted:"已中断",terminated:"已终止",cancelled:"已终止",completed:"已完成",approved:"已入库",succeeded:"已完成",rolled_back:"已回滚",approving:"正在入库",failed:"失败"};
const groups: [string,string,string[]][] = [["all","全部",[]],["active","进行中",["queued","running","sending","verifying","cancelling","pausing","terminating"]],["review","待确认",["prepared","needs_review","awaiting_reply"]],["paused","已暂停",["paused"]],["terminated","已终止",["terminated","cancelled"]],["completed","已完成",["completed","approved","succeeded","rolled_back"]],["failed","失败",["failed","interrupted"]]];
const stageLabels: Record<string,string> = {prepared:"待确认提示词",reply:"等待确认回复",plot:"剧情大纲",characters:"角色",settings:"设定",style:"语言分析",era_background:"时代背景事件",cleaning:"正文清理",download:"正在下载",pull:"正在下载",verify:"正在校验",fetch:"正在检索",search:"正在检索",parse:"正在解析",apply:"正在应用",complete:"已完成",chunking:"正文切块",queued:"等待开始",extracting:"正在提取",embedding:"正在匹配",rollback:"正在回滚",cache:"正在修复缓存"};
const active = (status: string) => groups[1][2].includes(status);

export default function TaskManager({onOpen,onCreate}:{onOpen:(task: ManagedTask,navigation?:TaskNavigation)=>void;onCreate:()=>void}) {
  const [page,setPage]=useState<TaskPage|null>(null),[query,setQuery]=useState(""),[debounced,setDebounced]=useState(""),[kind,setKind]=useState(""),[status,setStatus]=useState("all"),[offset,setOffset]=useState(0);
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(""),[error,setError]=useState("");
  const request=useRef<AbortController|null>(null),ticket=useRef(0);
  useEffect(()=>{const timer=window.setTimeout(()=>{setDebounced(query.trim());setOffset(0);},200);return()=>window.clearTimeout(timer);},[query]);
  const load=useCallback(async(quiet=false)=>{
    request.current?.abort();const controller=new AbortController();request.current=controller;const id=++ticket.current;
    if(!quiet)setLoading(true);
    try {const params=new URLSearchParams({offset:String(offset),limit:"30",...(debounced?{q:debounced}:{}),...(kind?{kind}:{}),...(status!=="all"?{status}: {})});const result=await studio<TaskPage>(`/tasks?${params}`,{signal:controller.signal});if(id===ticket.current&&!controller.signal.aborted){setPage(result);setError("");}}
    catch(e){if(!controller.signal.aborted&&id===ticket.current)setError(messageOf(e));}
    finally{if(id===ticket.current)setLoading(false);}
  },[debounced,kind,status,offset]);
  useEffect(()=>{void load();return()=>request.current?.abort();},[load]);
  useEffect(()=>{const refresh=()=>void load(true);window.addEventListener("inkreference:jobs-refresh",refresh);return()=>window.removeEventListener("inkreference:jobs-refresh",refresh);},[load]);
  const running=Object.entries(page?.counts||{}).some(([key,count])=>active(key)&&count>0);
  useEffect(()=>{if(!running)return;const timer=window.setInterval(()=>{if(!document.hidden&&window.location.hash==="#jobs")void load(true);},1500);return()=>window.clearInterval(timer);},[running,load]);
  const control=async(task:ManagedTask,action:"pause"|"resume"|"terminate")=>{
    setBusy(`${task.kind}:${task.id}:${action}`);setError("");
    try {const result=await send<{task:ManagedTask;action?:TaskNavigation}>(`/tasks/${encodeURIComponent(task.kind)}/${encodeURIComponent(task.id)}/${action}`,{});await load(true);if(action==="resume"&&result.action)onOpen(result.task,result.action);}
    catch(e){setError(messageOf(e));}finally{setBusy("");}
  };
  const count=(value:string)=>{if(!page)return 0;if(value==="all")return Object.values(page.counts).reduce((n,v)=>n+v,0);const bucket=groups.find(group=>group[0]===value);return bucket ? bucket[2].reduce((n,key)=>n+(page.counts[key]||0),0) : page.counts[value]||0;};
  return <section className="surface managed-tasks">
    <div className="managed-task-tools"><label className="managed-task-search"><Search size={17}/><input aria-label="搜索任务" placeholder="搜索任务" value={query} onChange={e=>setQuery(e.target.value)}/></label><select aria-label="任务类型" value={kind} onChange={e=>{setKind(e.target.value);setOffset(0);}}><option value="">全部类型</option>{Object.entries(kinds).map(([key,label])=><option value={key} key={key}>{label}</option>)}</select><button className="icon-button" aria-label="刷新任务" disabled={loading||!!busy} onClick={()=>void load()}><RefreshCw size={16}/></button><button className="button primary" onClick={onCreate}><Plus size={16}/>新建解析</button></div>
    <div className="managed-task-tabs" role="tablist" aria-label="任务状态">{groups.map(([value,label])=><button role="tab" aria-selected={status===value} className={status===value?"active":""} key={value} onClick={()=>{setStatus(value);setOffset(0);}}>{label}<span>{count(value)}</span></button>)}</div>
    <Notice error>{error}</Notice>{(loading||!!busy)&&<Busy label={busy?"正在更新任务":"正在读取任务"} compact/>}
    {!page?.items.length&&!loading?<Empty icon={ListTodo} title={query||kind||status!=="all"?"没有匹配的任务":"暂无任务"}/>:<div className="managed-task-list">{page?.items.map(task=>{const Icon=icons[task.kind as keyof typeof icons]||ListTodo;const model=task.model||task.profile_id;return <article key={`${task.kind}:${task.id}`} className={`managed-task-row task-${task.status}`}>
      <button className="managed-task-open" onClick={()=>onOpen(task)}><span className="managed-task-symbol"><Icon size={21}/></span><span className="managed-task-copy"><span className="managed-task-heading"><strong>{task.title||kinds[task.kind]||"任务"}</strong><span className={`managed-task-status ${task.status}`}>{labels[task.status]||task.status}</span></span><span className="managed-task-meta"><span>{kinds[task.kind]||task.kind}</span>{model&&<span title={model}>{model}</span>}<time>{dateLabel(task.updated_at||task.created_at)}</time></span><Progress completed={task.progress===null?0:Math.round(task.progress*100)} total={100} busy={active(task.status)} stage={stageLabels[task.stage]||labels[task.stage]||labels[task.status]||"正在处理"}/>{task.error&&<span className="managed-task-error">{task.error}</span>}</span></button>
      <div className="managed-task-controls">{task.capabilities.pause&&<button className="icon-button task-control pause" title="暂停" aria-label={`暂停 ${task.title}`} disabled={!!busy} onClick={()=>void control(task,"pause")}><Pause size={17} fill="currentColor"/></button>}{task.capabilities.resume&&<button className="icon-button task-control resume" title="恢复" aria-label={`恢复 ${task.title}`} disabled={!!busy||task.in_flight} onClick={()=>void control(task,"resume")}><Play size={17} fill="currentColor"/></button>}{task.capabilities.terminate&&<button className="icon-button task-control terminate" title="终止" aria-label={`终止 ${task.title}`} disabled={!!busy} onClick={()=>void control(task,"terminate")}><Square size={14} fill="currentColor"/></button>}</div>
    </article>;})}</div>}
    <footer className="managed-task-pagination"><span>{page?.total||0} 项{page&&page.total>0?` · ${offset+1}–${Math.min(offset+page.items.length,page.total)}`:""}</span><div><button className="button secondary" aria-label="上一页任务" disabled={!offset||loading} onClick={()=>setOffset(Math.max(0,offset-30))}><ChevronLeft size={16}/>上一页</button><button className="button secondary" aria-label="下一页任务" disabled={!page||offset+page.items.length>=page.total||loading} onClick={()=>setOffset(offset+30)}>下一页<ChevronRight size={16}/></button></div></footer>
  </section>;
}
