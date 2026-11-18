import { useEffect, useState } from 'react';
import { Check, Download, RefreshCw, X } from 'lucide-react';
import { send, studio, type ModelProfile } from './studio';
import { Busy, Modal, Notice, messageOf } from './ui';
import './embedding-downloads.css';

type Job = { id: string; model: string; endpoint: string; status: string; stage: string; completed: number; total: number | null; embedding_dimensions: number | null; error: string | null; registered_profile_id: string | null };
const active = (job: Job) => ['queued', 'running', 'verifying', 'cancelling'].includes(job.status);
const bytes = (size: number) => { const units = ['B', 'KB', 'MB', 'GB', 'TB']; let unit = 0; while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit++; } return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`; };

export default function EmbeddingDownloads({ profiles, onClose, onRegistered }: { profiles: ModelProfile[]; onClose: () => void; onRegistered: (profile: ModelProfile) => void }) {
  const local = profiles.filter(profile => profile.provider === 'ollama' && profile.connection_mode === 'local');
  const [profileId, setProfileId] = useState(local[0]?.id || '');
  const [endpoint, setEndpoint] = useState('http://127.0.0.1:11434');
  const [model, setModel] = useState('embeddinggemma');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const running = jobs.some(active);
  useEffect(() => {
    let alive = true;
    const refresh = async () => { try { const result = await studio<Job[]>('/embedding-downloads/jobs'); if (alive) setJobs(result); } catch (failure) { if (alive) setError(messageOf(failure)); } finally { if (alive) setLoading(false); } };
    void refresh(); const timer = running ? window.setInterval(() => { void refresh(); }, 900) : null;
    return () => { alive = false; if (timer !== null) window.clearInterval(timer); };
  }, [running]);
  const create = async () => {
    setBusy('create'); setError('');
    try {
      const values: Record<string, string> = { model: model.trim(), ...(profileId ? { profile_id: profileId } : { base_url: endpoint.trim() }) };
      const target = await studio<{ fingerprint: string }>(`/embedding-downloads/targets?${new URLSearchParams(values)}`);
      const job = await send<Job>('/embedding-downloads/jobs', { ...values, expected_target_fingerprint: target.fingerprint });
      setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)]);
    } catch (failure) { setError(messageOf(failure)); } finally { setBusy(''); }
  };
  const act = async (job: Job, operation: 'cancel' | 'retry' | 'register') => {
    setBusy(job.id); setError('');
    try {
      if (operation === 'register') {
        const result = await send<{ job: Job; profile: ModelProfile }>(`/embedding-downloads/jobs/${job.id}/register`, {});
        setJobs(previous => previous.map(item => item.id === job.id ? result.job : item)); onRegistered(result.profile);
      } else {
        const result = await send<Job>(`/embedding-downloads/jobs/${job.id}/${operation}`, {});
        setJobs(previous => previous.map(item => item.id === job.id ? result : item));
      }
    } catch (failure) { setError(messageOf(failure)); } finally { setBusy(''); }
  };
  return <Modal title="下载向量模型" onClose={onClose} wide><div className="modal-body embedding-download-panel">
    <Notice error={!!error}>{error}</Notice>
    <form className="embedding-download-form" onSubmit={event => { event.preventDefault(); void create(); }}><fieldset disabled={!!busy || running || loading}>
      <label className="studio-field"><span>Ollama 连接</span><select aria-label="下载 Ollama 连接" value={profileId} onChange={event => setProfileId(event.target.value)}><option value="">本机地址</option>{local.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
      {!profileId && <label className="studio-field"><span>本机服务地址</span><input aria-label="下载服务地址" value={endpoint} onChange={event => setEndpoint(event.target.value)} required spellCheck={false} /></label>}
      <label className="studio-field embedding-model-name"><span>Embedding 模型</span><input aria-label="下载 Embedding 模型" value={model} onChange={event => setModel(event.target.value)} required maxLength={220} placeholder="embeddinggemma" spellCheck={false} /></label>
      <button className="button primary" type="submit" disabled={!model.trim()}><Download size={16} />下载</button>
    </fieldset></form>
    {busy === 'create' && <Busy label="创建下载任务" delay={0} />}
    <div className="embedding-download-history" aria-label="向量模型下载任务">
      {loading ? <Busy label="读取下载任务" /> : !jobs.length ? <div className="embedding-download-empty"><Download size={25} /><span>暂无下载</span></div> : jobs.map(job => {
        const ratio = job.total ? Math.min(100, job.completed * 100 / job.total) : null;
        const registered = !!job.registered_profile_id && profiles.some(profile => profile.id === job.registered_profile_id);
        return <article className={`embedding-download-card ${job.status}`} key={job.id}>
          <header><div><strong>{job.model}</strong><span>{job.endpoint}</span></div><div className="embedding-download-actions">
            {active(job) && <button className="icon-button danger" aria-label={`取消下载 ${job.model}`} title="取消下载" disabled={busy === job.id || job.status === 'cancelling'} onClick={() => void act(job, 'cancel')}><X size={18} /></button>}
            {['failed', 'cancelled', 'paused'].includes(job.status) && <button className="icon-button" aria-label={`重试下载 ${job.model}`} title="重试下载" disabled={!!busy || running} onClick={() => void act(job, 'retry')}><RefreshCw size={17} /></button>}
            {job.status === 'completed' && (registered ? <span className="embedding-registered"><Check size={15} />已添加</span> : <button className="button secondary" disabled={!!busy} onClick={() => void act(job, 'register')}><Check size={15} />添加模型</button>)}
          </div></header>
          <div className="embedding-download-status"><span>{job.stage}</span><strong>{ratio === null ? '' : `${ratio.toFixed(1)}%`}</strong></div>
          {active(job) && (ratio !== null && job.status !== 'verifying' ? <div className="embedding-byte-progress" role="progressbar" aria-label={`${job.model} 下载进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={ratio}><i style={{ width: `${ratio}%` }} /></div> : <div className="indeterminate" role="progressbar" aria-label={job.stage}><i /></div>)}
          <footer><span>{job.total === null ? '—' : `${bytes(job.completed)} / ${bytes(job.total)}`}</span>{job.embedding_dimensions && <span>{job.embedding_dimensions} 维</span>}</footer>
          {job.error && <p className="embedding-download-error" role="alert">{job.error}</p>}
        </article>;
      })}
    </div>
  </div></Modal>;
}
