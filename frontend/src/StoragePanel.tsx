import { useEffect, useState } from "react";
import { Database, RefreshCw, Wrench } from "lucide-react";
import { send, studio } from "./studio";
import { Busy, Notice, messageOf } from "./ui";
import "./storage-panel.css";

type DatabaseInfo = {name: string; bytes: number; wal_bytes: number; reusable_bytes?: number; status: string; missing_indexes?: string[]; diagnostic_id?: string};
type Health = {databases: DatabaseInfo[]; disk_free_bytes?: number; index_plan_version?: number};
const size = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB`;
const names: Record<string,string> = {"inkreference.sqlite3":"素材库", "workbench.sqlite3":"文本与分析", "skills.sqlite3":"Skills", "workflow_definitions.sqlite3":"工作流", "checkpoints.sqlite3":"任务检查点"};
export default function StoragePanel() {
  const [health, setHealth] = useState<Health | null>(null), [busy, setBusy] = useState(""), [error, setError] = useState(""), [message, setMessage] = useState("");
  const load = async () => { setBusy("读取存储状态"); setError(""); try { setHealth(await studio<Health>("/storage")); } catch(e) { setError(messageOf(e)); } finally { setBusy(""); } };
  useEffect(() => { void load(); }, []);
  const optimize = async () => { setBusy("优化数据库"); setError(""); setMessage(""); try { const result = await send<{items: {name?: string; status: string; diagnostic_id?: string}[]}>("/storage/optimize", {database:"all"}); setHealth(await studio<Health>("/storage")); const failed = result.items.filter(item => item.status === "error"), deferred = result.items.filter(item => item.status === "deferred").length; if (failed.length) setError(failed.map(item => `${names[item.name || ""] || item.name || "数据库"}优化失败${item.diagnostic_id ? ` · ${item.diagnostic_id}` : ""}`).join("；")); else setMessage(deferred ? `${deferred} 个数据库尚未完成优化，请稍后重试` : "数据库优化完成"); } catch(e) { setError(messageOf(e)); } finally { setBusy(""); } };
  return <section className="surface storage-panel"><header><h2><Database size={19} />存储管理</h2><div><button className="icon-button" aria-label="刷新存储状态" disabled={!!busy} onClick={() => void load()}><RefreshCw size={15}/></button><button className="button secondary" disabled={!!busy} onClick={() => void optimize()}><Wrench size={15}/>优化数据库</button></div></header><Notice error={!!error} success={!!message}>{error || message}</Notice>{busy && <Busy label={busy} compact />}{health && <><div className="storage-metrics"><span>数据库 <strong>{size(health.databases.reduce((n,d)=>n+d.bytes+d.wal_bytes,0))}</strong></span>{health.disk_free_bytes != null && <span>磁盘可用 <strong>{size(health.disk_free_bytes)}</strong></span>}</div><div className="storage-database-list">{health.databases.map(db => <article key={db.name}><strong>{names[db.name] || db.name}</strong><span>{size(db.bytes)}</span><span>{db.status === "ready" ? (db.missing_indexes?.length ? `${db.missing_indexes.length} 个索引待优化` : "正常") : "暂不可读"}{db.diagnostic_id && <small> · {db.diagnostic_id}</small>}</span></article>)}</div></>}</section>;
}
