import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Download, Pause, Play, RefreshCw, Sparkles } from "lucide-react";
import { download } from "./api";
import { isRemoteModel, modelEndpoint } from "./model-security";
import { normalizeList, send, studio, type DocumentMeta, type List, type ModelProfile } from "./studio";
import { Busy, Modal, Notice, Progress, messageOf } from "./ui";
import {
  ChronicleCharacters, ChronicleClues, ChronicleEvents, ChronicleRelationships, ChronicleSettings, ChronicleStorylines, Original,
} from "./ChronicleViews";
import type {
  Answer, AnswerChoice, AnswerScope, ChronicleDecision, ChronicleQuestion, ChronicleRun, ChronicleState, SourceLine, ViewSource,
} from "./chronicle-types";
import "./chronicle.css";

const views = [
  ["events", "事件"], ["storylines", "故事线"], ["characters", "人物"], ["relationships", "人物关系"],
  ["settings", "设定"], ["foreshadowing", "伏笔"], ["questions", "校准"],
] as const;
const choices: Record<AnswerChoice, string> = { A: "A", B: "B", custom: "自填", defer: "暂缓" };
const scopes: Record<AnswerScope, string> = { item: "本条", book: "本书", similar: "同类作品" };
const runLabels: Record<string, string> = { queued: "等待执行", running: "拆书中", completed: "已完成", failed: "任务失败", paused: "已暂停" };
const isActive = (run?: ChronicleRun | null) => run?.status === "queued" || run?.status === "running";
type AnswerDraft = Omit<Answer, "choice"> & { choice: AnswerChoice | "" };
const freshAnswer: AnswerDraft = { choice: "", note: "", scope: "item" };

type Props = {
  doc: DocumentMeta;
  onOpenModels: () => void;
  legacy: (onDirty: (dirty: boolean) => void) => ReactNode;
  onDirty: (dirty: boolean) => void;
};

export function Chronicle({ doc, legacy, onOpenModels, onDirty }: Props) {
  const path = `/documents/${encodeURIComponent(doc.id)}/chronicle`;
  const [state, setState] = useState<ChronicleState | null>(null);
  const loadedOnce = useRef(false);
  const [view, setView] = useState<typeof views[number][0]>("events");
  const [storyline, setStoryline] = useState("");
  const [legacyDirty, setLegacyDirty] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, AnswerDraft>>({});
  const [startOpen, setStartOpen] = useState(false);
  const [source, setSource] = useState<{ lines: number[]; title: string; versionId: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const ticket = useRef(0);
  const run = state?.run;
  const result = state?.result;
  const active = isActive(run);
  const dirty = legacyDirty || Object.keys(drafts).length > 0;
  const questions = result?.questions || [];
  const decisions = state?.decisions || [];
  const pending = questions.filter(q => !decisions.some(d => d.question_id === q.id && d.choice === "defer")).length;

  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    async function load() {
      const current = ++ticket.current;
      try {
        const next = await studio<ChronicleState>(path);
        if (cancelled || ticket.current !== current) return;
        setState(next);
        setError("");
      } catch (e) {
        if (!cancelled && ticket.current === current) setError(messageOf(e));
      } finally {
        if (!cancelled && ticket.current === current) loadedOnce.current = true;
        if (active && !cancelled) timer = window.setTimeout(load, 1300);
      }
    }
    if (!busy) {
      if (active) timer = window.setTimeout(load, 800);
      else if (!loadedOnce.current) void load();
    }
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [path, active, busy]);

  const perform = async (operation: string, action: () => Promise<ChronicleState>, notice = "") => {
    const current = ++ticket.current;
    setBusy(operation); setError(""); setMessage("");
    try {
      const next = await action();
      if (ticket.current !== current) return false;
      setState(next);
      loadedOnce.current = true;
      setMessage(notice);
      return true;
    } catch (e) {
      if (ticket.current === current) setError(messageOf(e));
      return false;
    } finally {
      if (ticket.current === current) setBusy("");
    }
  };
  const showSource: ViewSource = (lines, title) => {
    if (state) setSource({ title, lines, versionId: state.version_id });
  };
  const clearAnswer = (id: string) => setDrafts(previous => {
    const next = { ...previous };
    delete next[id];
    return next;
  });
  const submitAnswer = async (question: ChronicleQuestion, answer: Answer) => {
    const saved = await perform("answer", () => send<ChronicleState>(`${path}/calibrations/${encodeURIComponent(question.id)}`, {
      ...answer,
      ...(answer.choice === "defer" ? { scope: "item", note: "" } : {}),
      expected_revision: state?.revision, version_id: state?.version_id,
      expected_model_fingerprint: state?.model?.target_fingerprint,
    }), answer.choice === "defer" ? "已暂缓此项" : "校准决定已提交");
    if (saved) clearAnswer(question.id);
  };
  const controlRun = (action: "resume" | "pause") => {
    if (!run) return;
    void perform(action, () => send<ChronicleState>(`${path}/runs/${encodeURIComponent(run.id)}/${action}`, {}));
  };
  const exportOutline = async () => {
    if (!state) return;
    setBusy("export"); setError("");
    try {
      await download(`/studio${path}/outline?version_id=${encodeURIComponent(state.version_id)}`, `${doc.title}_编年史.json`);
    } catch (e) { setError(messageOf(e)); }
    finally { setBusy(""); }
  };
  return <section className="chronicle">
    <header className="ref-section-heading chronicle-heading">
      <div><h3>编年史</h3>{result && <span className="chronicle-count">{result.events.length} 个事件</span>}</div>
      <div className="inline-actions">
        {state && <button className="icon-button" aria-label="刷新编年史" disabled={!!busy || active} onClick={() => void perform("reload", () => studio<ChronicleState>(path))}><RefreshCw size={15} /></button>}
        {result && <button className="button secondary" onClick={() => void exportOutline()} disabled={!!busy}><Download size={15} />导出编年史</button>}
        <button className="button primary" onClick={() => setStartOpen(true)} disabled={active || !!busy || dirty || !doc.latest_version_id || !state}><Sparkles size={15} />{result ? "重新拆书" : "全书拆书"}</button>
      </div>
    </header>
    <Notice error={!!error} success={!error && !!message}>{error || message}</Notice>
    {!state && (error ? <button className="button secondary" onClick={() => void perform("reload", () => studio<ChronicleState>(path))}><RefreshCw size={14} />重新读取</button> : <Busy label="正在读取编年史" compact inline />)}
    {busy === "answer" && <Busy label="正在提交校准决定" compact inline />}
    {busy === "export" && <Busy label="正在导出编年史" compact inline />}
    {busy === "reload" && <Busy label="正在读取编年史" compact inline />}
    {(busy === "resume" || busy === "pause") && <Busy label={busy === "resume" ? "正在恢复拆书" : "正在暂停拆书"} compact inline />}
    {run && <div className="chronicle-run">
      <div className="chronicle-run-actions">
        <strong>{runLabels[run.status] || run.status}</strong>
        {active && <button className="button secondary" onClick={() => controlRun("pause")} disabled={!!busy || run.stage === "正在暂停"}><Pause size={14} />暂停拆书</button>}
        {(run.status === "paused" || run.status === "failed") && <button className="button secondary" onClick={() => controlRun("resume")} disabled={!!busy}><Play size={14} />继续拆书</button>}
      </div>
      {(active || run.status === "paused") && <Progress stage={run.stage} completed={run.current} total={run.total} busy={active} />}
      <Notice error>{run.error}</Notice>
    </div>}
    {result && state.version_id !== doc.latest_version_id && <p className="chronicle-edition">当前显示上一版正文的拆书结果。可用当前正文重新拆书。</p>}
    {state && !result && !active && <div className="ref-empty">选择模型后开始全书拆书，结果将按叙述顺序整理在这里。</div>}
    {result && <>
      <div className="chronicle-views" role="group" aria-label="拆书结果">{views.map(([key, label]) => <button type="button" key={key} className={view === key ? "active" : ""} aria-pressed={view === key} onClick={() => { setView(key); if (key === "events") setStoryline(""); }}>{label}{key === "questions" && pending > 0 && <span>{pending}</span>}</button>)}</div>
      <div className="chronicle-view-body">
        {view === "events" && <ChronicleEvents result={result} storyline={storyline} onStoryline={setStoryline} onView={showSource} />}
        {view === "storylines" && <ChronicleStorylines result={result} showEvents={id => { setStoryline(id); setView("events"); }} />}
        {view === "characters" && <ChronicleCharacters result={result} onView={showSource} />}
        {view === "relationships" && <ChronicleRelationships result={result} onView={showSource} />}
        {view === "settings" && <ChronicleSettings result={result} onView={showSource} />}
        {view === "foreshadowing" && <ChronicleClues result={result} onView={showSource} />}
        {view === "questions" && <Calibration questions={questions} decisions={decisions} pending={pending} drafts={drafts}
          disabled={!!busy || active} onView={showSource} onSubmit={(question, answer) => void submitAnswer(question, answer)}
          onDraft={(id, answer) => setDrafts(previous => ({ ...previous, [id]: answer }))} onClear={clearAnswer} />}
      </div>
    </>}
    <details className="whole-chronicle-review"><summary>可编辑剧情资料</summary>{legacy(setLegacyDirty)}</details>
    {startOpen && <StartDialog onClose={() => setStartOpen(false)} onOpenModels={() => { setStartOpen(false); onOpenModels(); }} hasResult={!!result} onStart={async profile => {
      const next = await send<ChronicleState>(`${path}/runs`, {
        version_id: doc.latest_version_id, profile_id: profile.id, remote_consent: isRemoteModel(profile), expected_model_fingerprint: profile.target_fingerprint,
      });
      setState(next); setError(""); setMessage(""); setStartOpen(false);
    }} />}
    {source && <SourceDialog path={path} source={source} onClose={() => setSource(null)} />}
  </section>;
}

function StartDialog({ hasResult, onClose, onOpenModels, onStart }: {
  hasResult: boolean; onClose: () => void; onOpenModels: () => void; onStart: (profile: ModelProfile) => Promise<void>;
}) {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [busy, setBusy] = useState("读取");
  const [error, setError] = useState("");
  const profile = profiles.find(p => p.id === profileId);
  const remote = isRemoteModel(profile);
  useEffect(() => {
    let current = true;
    void studio<List<ModelProfile>>("/models").then(value => {
      if (!current) return;
      const next = normalizeList(value); setProfiles(next); setProfileId(next[0]?.id || "");
    }).catch(e => { if (current) setError(messageOf(e)); }).finally(() => { if (current) setBusy(""); });
    return () => { current = false; };
  }, []);
  return <Modal title={hasResult ? "重新拆书" : "全书拆书"} onClose={onClose} busy={busy === "提交"}>
    <div className="modal-body">
      <label className="studio-field"><span>模型配置</span><select aria-label="模型配置" value={profileId} disabled={!!busy} onChange={e => setProfileId(e.target.value)}>
        {!profiles.length && <option value="">{busy ? "正在读取模型" : "尚未配置模型"}</option>}
        {profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}
      </select></label>
      <button className="text-button" onClick={onOpenModels} disabled={busy === "提交"}>管理模型配置<ArrowRight size={14} /></button>
      {profile && <div className="ref-model-target"><span>{remote ? "远程 API" : "本机模型"}</span><strong>{modelEndpoint(profile.base_url, true)}</strong><small>{profile.model}</small></div>}
      {hasResult && <p>重新拆书完成后将更新当前编年史。</p>}
      {remote && <p>当前正文将发送至以上服务进行拆书。</p>}
      <Notice error>{error}</Notice>
      {busy && <Busy label={busy === "读取" ? "正在读取模型" : "正在建立拆书任务"} compact inline />}
    </div>
    <div className="modal-footer">
      <button className="button secondary" disabled={busy === "提交"} onClick={onClose}>取消</button>
      <button className="button primary" disabled={!!busy || !profile || (remote && !profile.target_fingerprint)} onClick={async () => {
        if (!profile) return;
        setBusy("提交"); setError("");
        try { await onStart(profile); }
        catch (e) { setError(messageOf(e)); }
        finally { setBusy(""); }
      }}>{remote ? "确认目标并开始" : "开始拆书"}</button>
    </div>
  </Modal>;
}

function SourceDialog({ path, source, onClose }: { path: string; source: { lines: number[]; title: string; versionId: string }; onClose: () => void }) {
  const [lines, setLines] = useState<SourceLine[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let current = true;
    const query = new URLSearchParams({ lines: source.lines.join(","), version_id: source.versionId });
    void studio<{ lines: SourceLine[] }>(`${path}/source?${query}`).then(next => { if (current) setLines(next.lines); })
      .catch(e => { if (current) setError(messageOf(e)); }).finally(() => { if (current) setLoaded(true); });
    return () => { current = false; };
  }, [path, source]);
  return <Modal title={source.title} wide onClose={onClose}>
    <div className="modal-body chronicle-source"><Notice error>{error}</Notice>
      {!loaded && <Busy label="正在读取原文" compact inline />}
      {lines.map(line => <figure key={line.line}><figcaption>L{line.line}{line.chapter && ` · ${line.chapter}`}</figcaption><blockquote>{line.text}</blockquote></figure>)}
    </div>
    <div className="modal-footer"><button className="button secondary" onClick={onClose}>关闭原文</button></div>
  </Modal>;
}

function Calibration({ questions, decisions, pending, drafts, disabled, onDraft, onClear, onSubmit, onView }: {
  questions: ChronicleQuestion[]; decisions: ChronicleDecision[]; pending: number; drafts: Record<string, AnswerDraft>; disabled: boolean;
  onDraft: (id: string, answer: AnswerDraft) => void; onClear: (id: string) => void; onSubmit: (question: ChronicleQuestion, answer: Answer) => void; onView: ViewSource;
}) {
  return <div className="chronicle-calibration">
    <p className="chronicle-count" aria-live="polite">{pending ? `待校准 ${pending} 项` : "没有待校准的条目"}</p>
    {questions.map(question => {
      const decision = decisions.find(d => d.question_id === question.id);
      const value = drafts[question.id] || freshAnswer;
      return <form key={question.id} className="chronicle-question" onSubmit={e => { e.preventDefault(); if (value.choice) onSubmit(question, { ...value, choice: value.choice }); }}>
        <h4>{question.question}{decision?.choice === "defer" && <span className="chronicle-tag">已暂缓</span>}</h4>
        <Original lines={question.source_lines} title={question.question} onView={onView} />
        <dl>{question.options.map(option => <div key={option.id}><dt>{option.id} · {option.label}</dt><dd>{option.effect}</dd></div>)}</dl>
        <div className="whole-chronicle-filters">
          <label className="chronicle-filter"><span>我的选择</span><select aria-label="我的选择" value={value.choice} disabled={disabled} onChange={e => onDraft(question.id, { ...value, choice: e.target.value as AnswerDraft["choice"] })}><option value="">请选择</option>{Object.entries(choices).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
          <label className="chronicle-filter"><span>适用范围</span><select aria-label="适用范围" value={value.choice === "defer" ? "item" : value.scope} disabled={disabled || value.choice === "defer"} onChange={e => onDraft(question.id, { ...value, scope: e.target.value as AnswerScope })}>{Object.entries(scopes).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        </div>
        {value.choice !== "defer" && <label className="studio-field"><span>{value.choice === "custom" ? "我的决定" : "补充说明"}</span><textarea aria-label={value.choice === "custom" ? "我的决定" : "补充说明"} rows={2} maxLength={2000} disabled={disabled} value={value.note} onChange={e => onDraft(question.id, { ...value, note: e.target.value })} /></label>}
        <div className="inline-actions">
          <button className="button primary" disabled={disabled || !value.choice || (value.choice === "custom" && !value.note.trim())} type="submit">{value.choice === "defer" ? "暂缓此项" : "应用校准"}</button>
          {drafts[question.id] && <button className="text-button" type="button" disabled={disabled} onClick={() => onClear(question.id)}>清除填写</button>}
        </div>
      </form>;
    })}
    {decisions.length > 0 && <details className="chronicle-applied"><summary>校准记录</summary><ul>{decisions.map(d => <li key={d.question_id}><strong>{d.question}</strong><span>{choices[d.choice]} · {scopes[d.scope]}</span>{d.note && <p>{d.note}</p>}</li>)}</ul></details>}
  </div>;
}
