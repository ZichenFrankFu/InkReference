import { useEffect, useRef, useState } from 'react';
import { Copy, Download, FileText, Plus, Save, Sparkles, Trash2 } from 'lucide-react';
import { studio, send, normalizeList, type ModelProfile } from './studio';
import { Busy, Empty, Modal, Notice, messageOf } from './ui';
import { isRemoteModel, modelEndpoint } from './model-security';
import './volume-panel.css';

type Volume = { title: string; start_chapter: number; end_chapter: number };
type Plan = { revision: number; chapter_count: number; volumes: Volume[]; method: string };
export function VolumePanel({ documentId, versionId }: { documentId: string; versionId: string }) {
  const [plan,setPlan] = useState<Plan|null>(null), [rows,setRows] = useState<Volume[]>([]);
  const [busy,setBusy] = useState(''),[error,setError]=useState(''),[message,setMessage]=useState('');
  const [paste,setPaste]=useState(''),[showPaste,setShowPaste]=useState(false),[prompt,setPrompt]=useState('');
  const [models,setModels]=useState<ModelProfile[]>([]),[profile,setProfile]=useState(''),[confirm,setConfirm]=useState(false);
  const ticket=useRef(0), base=`/documents/${documentId}/versions/${versionId}/volumes`;
  useEffect(()=>{const id=++ticket.current; setBusy('读取分卷');setError('');setPlan(null);setRows([]);
    Promise.all([studio<Plan>(base),studio<{items:ModelProfile[]}>('/models')]).then(([p,m])=>{
      if(id===ticket.current){setPlan(p);setRows(p.volumes);const choices=normalizeList(m);setModels(choices);setProfile(choices[0]?.id||'');}
    }).catch(e=>{if(id===ticket.current)setError(messageOf(e));}).finally(()=>{if(id===ticket.current)setBusy('');});
    return()=>{ticket.current++;};
  },[base]);
  const run=async(label:string,action:()=>Promise<void>)=>{setBusy(label);setError('');setMessage('');try{await action();}catch(e){setError(messageOf(e));}finally{setBusy('');}};
  const selected=models.find(m=>m.id===profile),dirty=!!plan&&JSON.stringify(rows)!==JSON.stringify(plan.volumes);
  const detect=()=>run('识别分卷',async()=>{const result=await send<{volumes:Volume[]}>(`${base}/detect`,{});setRows(result.volumes);setMessage('分卷预览已更新');});
  const modelDetect=()=>run('模型识别分卷',async()=>{setConfirm(false);const result=await send<{volumes:Volume[]}>(`${base}/model`,{profile_id:profile,remote_consent:!!selected&&isRemoteModel(selected),expected_model_fingerprint:selected?.target_fingerprint});setRows(result.volumes);setMessage('分卷预览已更新');});
  const save=()=>run('保存分卷',async()=>{const next=await send<Plan>(base,{volumes:rows,expected_revision:plan?.revision??0},'PUT');setPlan(next);setRows(next.volumes);setMessage('分卷已保存');});
  const copy=()=>run('准备提示词',async()=>{const result=await studio<{prompt:string}>(`${base}/prompt`);try{await navigator.clipboard.writeText(result.prompt);setMessage('提示词已复制');}catch{setPrompt(result.prompt);}});
  return <section className="volume-panel" aria-label="分卷规划">
    <div className="volume-toolbar"><div><h3>分卷规划</h3><span>{plan?.chapter_count??'—'} 章 · {rows.length} 卷{dirty?' · 未保存':''}</span></div>
      <button disabled={!!busy||!plan} onClick={detect}><Sparkles size={15}/>自动识别</button><button disabled={!!busy||!plan} onClick={copy}><Copy size={15}/>复制提示词</button><button disabled={!!busy||!plan} onClick={()=>setShowPaste(true)}>粘贴结果</button>
      <button className="primary" disabled={!!busy||!dirty} onClick={save}><Save size={15}/>保存分卷</button></div>
    {error&&<Notice error>{error}</Notice>}{message&&<Notice>{message}</Notice>}{busy&&<Busy label={busy}/>}
    <div className="volume-model"><label>识别模型<select value={profile} onChange={e=>setProfile(e.target.value)} disabled={!!busy}><option value="">选择模型</option>{models.map(m=><option value={m.id} key={m.id}>{m.name}</option>)}</select></label><button disabled={!!busy||!selected||!plan} onClick={()=>selected&&isRemoteModel(selected)?setConfirm(true):modelDetect()}>模型识别</button></div>
    <div className="volume-table"><div className="volume-row volume-head"><span>卷名</span><span>起始章</span><span>结束章</span><span/></div>
    {rows.map((row,i)=><div className="volume-row" key={i}><input aria-label={`第${i+1}卷名称`} value={row.title} disabled={!!busy} onChange={e=>setRows(rows.map((r,j)=>j===i?{...r,title:e.target.value}:r))}/><input aria-label={`第${i+1}卷起始章`} type="number" min={1} max={plan?.chapter_count} value={row.start_chapter} disabled={!!busy} onChange={e=>setRows(rows.map((r,j)=>j===i?{...r,start_chapter:Number(e.target.value)}:r))}/><input aria-label={`第${i+1}卷结束章`} type="number" min={1} max={plan?.chapter_count} value={row.end_chapter} disabled={!!busy} onChange={e=>setRows(rows.map((r,j)=>j===i?{...r,end_chapter:Number(e.target.value)}:r))}/><button className="icon" aria-label={`删除第${i+1}卷`} disabled={!!busy} onClick={()=>setRows(rows.filter((_,j)=>j!==i))}><Trash2 size={15}/></button></div>)}
    </div><button disabled={!!busy||!plan} onClick={()=>setRows([...rows,{title:`第${rows.length+1}卷`,start_chapter:(rows.at(-1)?.end_chapter??0)+1,end_chapter:plan?.chapter_count??1}])}><Plus size={15}/>添加分卷</button>
    {showPaste&&<Modal title="粘贴分卷结果" busy={!!busy} onClose={()=>setShowPaste(false)}><textarea aria-label="分卷 JSON" className="volume-paste" value={paste} onChange={e=>setPaste(e.target.value)} placeholder={'{"volumes":[{"title":"第一卷","start_chapter":1,"end_chapter":30}]}'}/><div className="modal-actions"><button onClick={()=>setShowPaste(false)}>取消</button><button className="primary" disabled={!!busy||!paste.trim()} onClick={()=>run('校验分卷结果',async()=>{const result=await send<{volumes:Volume[]}>(`${base}/paste`,{content:paste});setRows(result.volumes);setShowPaste(false);setPaste('');})}>预览分卷</button></div>{error&&<Notice error>{error}</Notice>}</Modal>}
    {prompt&&<Modal title="分卷提示词" onClose={()=>setPrompt('')}><textarea className="volume-paste" aria-label="分卷提示词" readOnly value={prompt} onFocus={e=>e.target.select()}/></Modal>}
    {confirm&&selected&&<Modal title="使用远程模型识别分卷" busy={!!busy} onClose={()=>setConfirm(false)}><p>将章节目录发送至 {modelEndpoint(selected.base_url)} · {selected.model}</p><div className="modal-actions"><button onClick={()=>setConfirm(false)}>取消</button><button className="primary" onClick={modelDetect}>开始识别</button></div></Modal>}
  </section>;
}

type Source = { id:string; filename:string; bytes?:number; original_bytes?:number; created_at?:string; sha256?:string; original_sha256?:string };
export function SourceFiles({documentId,versionId}:{documentId:string;versionId?:string}) {
  const [files,setFiles]=useState<Source[]>([]),[busy,setBusy]=useState(true),[error,setError]=useState('');
  useEffect(()=>{let active=true;setBusy(true);studio<{items:Source[]}>(`/documents/${documentId}/sources`).then(data=>{if(active)setFiles(normalizeList(data));}).catch(e=>{if(active)setError(messageOf(e));}).finally(()=>{if(active)setBusy(false);});return()=>{active=false;};},[documentId,versionId]);
  return <section className="source-files" aria-label="原始文件">{busy&&<Busy label="读取原始文件"/>}{error&&<Notice error>{error}</Notice>}{!busy&&!files.length&&<Empty icon={FileText} title="暂无原始文件"/>}{files.map((file,i)=><article className="source-file" key={file.id}><FileText size={22}/><div><strong>{file.filename}</strong><span>{i===0?'初始上传':'接续上传'}{file.original_bytes!==undefined||file.bytes!==undefined?` · ${((file.original_bytes??file.bytes??0)/1024).toFixed(1)} KB`:''}</span><code>{file.sha256||file.original_sha256}</code></div><a className="button" href={`/api/studio/documents/${documentId}/sources/${file.id}/download`} download><Download size={15}/>下载原件</a></article>)}</section>;
}
