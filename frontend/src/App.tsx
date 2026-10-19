import { lazy, memo, Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  BookOpen,
  FileText,
  FolderArchive,
  GitBranch,
  Layers3,
  PanelLeftClose,
  PanelLeftOpen,
  ListTodo,
  FileSearch,
  Monitor,
  Moon,
  Sun,
  Wrench,
  SlidersHorizontal,
} from "lucide-react";
import { Busy, Modal, Notice, PageActivityScope, PageNavigationScope, PageTitle } from "./ui";
import { download } from "./api";
import { studio } from "./studio";
import { readScheme, setScheme, type Scheme } from "./theme";
import "./theme.css";
import "./loading.css";
import "./app-icon.css";
const Library = memo(lazy(() => import("./Library")));
const Documents = memo(lazy(() => import("./Documents")));
const Jobs = memo(lazy(() => import("./Jobs")));
const ReferencePage = memo(lazy(() => import("./Jobs").then(module => ({ default: module.ReferencePage }))));
const Workflows = memo(lazy(() => import("./Workflows")));
const Models = memo(lazy(() => import("./Models")));
const QuickRepair = lazy(() => import("./QuickRepair"));
const StoragePanel = lazy(() => import("./StoragePanel"));
type Section =
  | "library"
  | "documents"
  | "reference"
  | "research"
  | "jobs"
  | "workflows"
  | "models"
  | "backup";
const sections: {
  id: Section;
  label: string;
  icon: typeof BookOpen;
}[] = [
  { id: "library", label: "作品库", icon: BookOpen },
  {
    id: "documents",
    label: "正文清理",
    icon: FileText,
  },
  { id: "reference", label: "作品解析", icon: FileSearch },
  { id: "jobs", label: "任务管理", icon: ListTodo },
  {
    id: "workflows",
    label: "Agent 与 Skills",
    icon: GitBranch,
  },
  {
    id: "models",
    label: "模型设置",
    icon: SlidersHorizontal,
  },
];
const initial = (): Section => {
  const hash = window.location.hash.slice(1);
  if (hash === "research") {
    try { sessionStorage.setItem("inkreference.catalog-focus", JSON.stringify({ section: "library", intake: true })); } catch { /* Optional focus state. */ }
    return "library";
  }
  if (hash === "jobs") {
    try {
      const focus = JSON.parse(sessionStorage.getItem("inkreference.catalog-focus") || "null");
      if (focus?.section === "jobs" && typeof focus.documentId === "string") return "reference";
    } catch { /* An obsolete focus request does not block startup. */ }
  }
  return [...sections.map((s) => s.id), "backup"].includes(hash)
    ? (hash as Section)
    : "library";
};
export default function App() {
  const [section, setSection] = useState<Section>(initial);
  useEffect(() => { if (window.location.hash === "#research") window.history.replaceState(null, "", "#library"); }, []);
  const [visited, setVisited] = useState<Section[]>(() => [initial()]);
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    if (!mobile) return;
    const previous = document.activeElement as HTMLElement | null;
    document.querySelector<HTMLButtonElement>('.sidebar .nav-item[aria-current="page"]')?.focus();
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setMobile(false); };
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("keydown", close); previous?.focus(); };
  }, [mobile]);
  const [collapsed, setCollapsed] = useState(() => window.matchMedia("(max-width: 1100px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1100px)");
    const compact = () => setCollapsed(media.matches);
    media.addEventListener("change", compact);
    return () => media.removeEventListener("change", compact);
  }, []);
  const [jobId, setJobId] = useState<string>();
  const [repairOpen, setRepairOpen] = useState(false);
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const appearanceTrigger = useRef<HTMLButtonElement>(null);
  const [scheme, changeScheme] = useState<Scheme>(readScheme);
  useEffect(() => {
    const open = () => { setRepairOpen(true); setMobile(false); setAppearanceOpen(false); };
    window.addEventListener("inkreference:debug-open", open);
    return () => window.removeEventListener("inkreference:debug-open", open);
  }, []);
  useEffect(() => {
    const sync = () => changeScheme(readScheme());
    window.addEventListener("inkreference:scheme-changed", sync);
    return () => window.removeEventListener("inkreference:scheme-changed", sync);
  }, []);
  useEffect(() => {
    if (!appearanceOpen) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") { setAppearanceOpen(false); appearanceTrigger.current?.focus(); } };
    document.querySelector<HTMLButtonElement>(".appearance-popover button.active")?.focus();
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [appearanceOpen]);
  const [modelReturn, setModelReturn] = useState<Section | null>(null);
  const sectionRef = useRef(section);
  useEffect(() => {
    if (window.location.hash === "#jobs" && sectionRef.current === "reference") window.history.replaceState(null, "", "#reference");
  }, []);
  const nav = useCallback((id: Section) => {
    if (id === "research") id = "library";
    setMobile(false);
    setAppearanceOpen(false);
    if (id === sectionRef.current) return;
    if (id === "models") setModelReturn(sectionRef.current);
    sectionRef.current = id;
    setSection(id);
    setVisited((v) => (v.includes(id) ? v : [...v, id]));
    window.history.replaceState(null, "", `#${id}`);
    window.dispatchEvent(new Event(`inkreference:${id}-refresh`));
  }, []);
  const onDocumentJob = useCallback((id: string) => {
    setJobId(id);
    nav("jobs");
  }, [nav]);
  const openMenu = useCallback(() => setMobile(true), []);
  useEffect(() => {
    const open = (event: Event) => {
      const incoming = (event as CustomEvent).detail;
      if (!incoming || !["library", "documents", "reference", "jobs", "research", "models", "workflows", "backup"].includes(incoming.section)) return;
      const target = incoming.section === "research" ? { ...incoming, section: "library", intake: true } : incoming.section === "jobs" && incoming.documentId ? { ...incoming, section: "reference" } : incoming;
      if (target.section === "jobs" && typeof target.runId === "string") setJobId(target.runId);
      // Keep the focus target until a lazy page has mounted and consumed it.
      try { sessionStorage.setItem("inkreference.catalog-focus", JSON.stringify(target)); } catch { /* focus event still works */ }
      nav(target.section as Section);
      window.dispatchEvent(new CustomEvent("inkreference:catalog-focus", { detail: target }));
    };
    window.addEventListener("inkreference:navigate", open);
    return () => window.removeEventListener("inkreference:navigate", open);
  }, [nav]);
  useEffect(() => {
    const update = () => {
      const key = window.location.hash.slice(1);
      if ([...sections.map((s) => s.id), "backup"].includes(key))
        nav(key as Section);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, [nav]);
  return (
    <div className={`app studio-app ${collapsed ? "sidebar-collapsed" : ""}`}>
      <a href="#workspace-content" className="skip-link">
        跳到工作区
      </a>
      {mobile && <div className="nav-shade" onClick={() => setMobile(false)} />}
      <aside
        id="app-navigation"
        className={`sidebar ${mobile ? "mobile-open" : ""}`}
        aria-label="工作区导航"
      >
        <div className="sidebar-brand-row">
        <button className="brand" onClick={() => nav("library")} title={collapsed ? "InkReference" : undefined}>
          <span className="brand-mark">
            <img src="/app-icon.svg" width="32" height="32" alt="" />
          </span>
          <span>InkReference</span>
        </button>
        <button className="icon-button sidebar-toggle desktop-collapse" onClick={() => setCollapsed(value => !value)} aria-label={collapsed ? "展开侧栏" : "收起侧栏"} aria-expanded={!collapsed} title={collapsed ? "展开侧栏" : "收起侧栏"}>
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </button>
        <button className="icon-button sidebar-mobile-close" onClick={() => setMobile(false)} aria-label="收起导航"><PanelLeftClose size={18} /></button>
        </div>
        <nav>
          {sections.map((s) => (
            <button
              key={s.id}
              title={collapsed ? s.label : undefined}
              className={`nav-item ${section === s.id ? "active" : ""}`}
              onClick={() => nav(s.id)}
              aria-current={section === s.id ? "page" : undefined}
            >
              <s.icon size={19} />
              <span>{s.label}</span>
              {section === s.id && <i />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button
            className={`nav-item ${section === "backup" ? "active" : ""}`}
            onClick={() => nav("backup")}
            title={collapsed ? "备份与恢复" : undefined}
            aria-current={section === "backup" ? "page" : undefined}
          >
            <FolderArchive size={18} />
            <span>备份与恢复</span>
          </button>
          <button className="nav-item repair-entry" onClick={() => { setRepairOpen(true); setMobile(false); setAppearanceOpen(false); }} title={collapsed ? "快捷修复" : undefined}>
            <Wrench size={18} />
            <span>快捷修复</span>
          </button>
          <div className="appearance-control">
            <button ref={appearanceTrigger} className="appearance-trigger" onClick={() => setAppearanceOpen((open) => !open)} aria-expanded={appearanceOpen} aria-controls="appearance-options" aria-label="外观" title={collapsed ? "外观" : undefined}>
              {scheme === "dark" ? <Moon size={17} /> : scheme === "light" ? <Sun size={17} /> : <Monitor size={17} />}
              <span>外观</span>
              <small>{scheme === "dark" ? "深色" : scheme === "light" ? "浅色" : "跟随系统"}</small>
            </button>
            {appearanceOpen && <>
              <button className="appearance-dismiss" aria-label="关闭外观选项" tabIndex={-1} onClick={() => setAppearanceOpen(false)} />
              <div id="appearance-options" className="appearance-popover" role="group" aria-label="颜色方案" onKeyDown={(event) => { if (event.key === "Escape") setAppearanceOpen(false); }}>
                {([{ id: "light", label: "浅色", icon: Sun }, { id: "dark", label: "深色", icon: Moon }, { id: "system", label: "跟随系统", icon: Monitor }] as const).map(({ id, label, icon: Icon }) => <button key={id} onClick={() => { setScheme(id); changeScheme(id); setAppearanceOpen(false); appearanceTrigger.current?.focus(); }} aria-pressed={scheme === id} className={scheme === id ? "active" : ""}><Icon size={16} /><span>{label}</span><span className={`scheme-preview ${id}`} /></button>)}
              </div>
            </>}
          </div>
        </div>
      </aside>
      <div className="workspace">
        <div id="workspace-content" className="workspace-content" tabIndex={-1}>
          {visited.map((page) => (
            <div
              key={page}
              hidden={section !== page}
              className="workspace-page"
            >
              <PageActivityScope active={section === page && !repairOpen}>
              <PageNavigationScope openMenu={openMenu} menuOpen={mobile} returnAction={page === "models" && modelReturn ? <button className="text-button model-return" onClick={() => nav(modelReturn)}>返回{sections.find(s => s.id === modelReturn)?.label || "备份与恢复"}</button> : undefined}>
              <Suspense fallback={<main className="studio-main workspace-loading"><PageTitle title={sections.find(s => s.id === page)?.label || "备份与恢复"} /><Busy label="正在打开工作区" delay={0} /></main>}>
                {page === "library" ? (
                  <Library />
                ) : page === "documents" ? (
                  <Documents
                    navigate={nav}
                    onJob={onDocumentJob}
                  />
                ) : page === "reference" ? (
                  <ReferencePage navigate={nav} />
                ) : page === "jobs" ? (
                  <Jobs initialId={jobId} navigate={nav} />
                ) : page === "workflows" ? (
                  <Workflows />
                ) : page === "models" ? (
                  <Models />
                ) : (
                  <Backup />
                )}
              </Suspense>
              </PageNavigationScope>
              </PageActivityScope>
            </div>
          ))}
        </div>
      </div>
      {repairOpen && <Suspense fallback={<Modal title="快捷修复" onClose={() => setRepairOpen(false)} busy><Busy label="正在打开快捷修复" delay={0} /></Modal>}><QuickRepair onClose={() => setRepairOpen(false)} /></Suspense>}
    </div>
  );
}
function Backup() {
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [confirm, setConfirm] = useState(false);
  const exportProject = async () => {
    setBusy("export");
    setError("");
    try {
      await download("/studio/projects/export", "inkreference-project.zip");
      setMessage("完整项目备份已准备好。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "备份失败");
    } finally {
      setBusy("");
    }
  };
  const exportTables = async () => {
    setBusy("tables");
    setError("");
    try {
      await download(
        "/studio/projects/tables/export",
        "InkReference-tables.zip",
      );
      setMessage("全部业务表 CSV 已准备好。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setBusy("");
    }
  };
  const restore = async () => {
    if (!restoreFile) return;
    setBusy("restore");
    setError("");
    try {
      const form = new FormData();
      form.append("file", restoreFile);
      const result = await studio<Record<string, unknown>>(
        "/projects/restore",
        { method: "POST", body: form },
      );
      setMessage(
        `已验证并恢复到独立目录：${String(result.data_dir || result.path || result.restored_path || result.restore_dir || "请查看服务日志")}。请通过启动命令 --data-dir 打开恢复的工作区。`,
      );
      setConfirm(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "恢复失败");
    } finally {
      setBusy("");
    }
  };
  return (
    <main className="studio-main">
      <PageTitle title="备份与恢复" />
      <div className="backup-grid">
        <section className="surface backup-card">
          <span className="feature-icon">
            <FolderArchive size={28} />
          </span>
          <h2>完整项目备份</h2>
          <button
            className="button primary"
            onClick={() => void exportProject()}
            disabled={!!busy}
          >
            下载项目备份
          </button>
        </section>
        <section className="surface backup-card">
          <span className="feature-icon">
            <Layers3 size={28} />
          </span>
          <h2>全部数据表 CSV</h2>
          <button
            className="button secondary"
            onClick={() => void exportTables()}
            disabled={!!busy}
          >
            导出全部业务表
          </button>
        </section>
        <section className="surface backup-card">
          <span className="feature-icon warm">
            <BookOpen size={28} />
          </span>
          <h2>从备份恢复</h2>
          <label className="button secondary file-button">
            选择备份 ZIP
            <input
              type="file"
              accept=".zip"
              onChange={(e) => {
                setRestoreFile(e.target.files?.[0] || null);
                setConfirm(!!e.target.files?.length);
              }}
              disabled={!!busy}
            />
          </label>
        </section>
      </div>
      {busy && (
        <Busy
          label={
            busy === "export"
              ? "正在生成完整备份"
              : busy === "tables"
                ? "正在导出全部数据表"
                : "正在验证并恢复归档"
          }
        />
      )}
      <Notice error={!!error} success={!error && !!message}>
        {error || message}
      </Notice>
      <Suspense fallback={<Busy label="读取存储管理" compact />}><StoragePanel /></Suspense>
      {confirm && (
        <Modal title="恢复备份" onClose={() => setConfirm(false)} busy={!!busy}>
          <div className="modal-body">
            <p>
              将从 <strong>{restoreFile?.name}</strong> 恢复一个独立工作区。
            </p>
            <p>当前正在使用的工作区会保留。</p>
          </div>
          <div className="modal-footer">
            <button
              className="button secondary"
              onClick={() => setConfirm(false)}
              disabled={!!busy}
            >
              取消
            </button>
            <button
              className="button primary"
              onClick={() => void restore()}
              disabled={!!busy}
            >
              验证并恢复
            </button>
          </div>
        </Modal>
      )}
    </main>
  );
}
