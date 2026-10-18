import { lazy, Suspense, useEffect, useState } from "react";
import {
  BookOpen,
  ChevronRight,
  FileText,
  FolderArchive,
  GitBranch,
  HardDrive,
  Layers3,
  Menu,
  PanelLeftClose,
  PenLine,
  Play,
  Settings2,
  SlidersHorizontal,
} from "lucide-react";
import Library from "./Library";
import { Busy, Modal, Notice } from "./ui";
import { download } from "./api";
import { studio } from "./studio";
const Documents = lazy(() => import("./Documents"));
const Jobs = lazy(() => import("./Jobs"));
const Workflows = lazy(() => import("./Workflows"));
const Models = lazy(() => import("./Models"));
type Section =
  | "library"
  | "documents"
  | "jobs"
  | "workflows"
  | "models"
  | "backup";
const sections: {
  id: Section;
  label: string;
  icon: typeof BookOpen;
}[] = [
  { id: "library", label: "素材库", icon: Layers3 },
  {
    id: "documents",
    label: "全文与清洗",
    icon: FileText,
  },
  { id: "jobs", label: "分析与审核", icon: Play },
  {
    id: "workflows",
    label: "工作流与 Skills",
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
  return [...sections.map((s) => s.id), "backup"].includes(hash)
    ? (hash as Section)
    : "library";
};
export default function App() {
  const [section, setSection] = useState<Section>(initial);
  const [visited, setVisited] = useState<Section[]>([initial()]);
  const [mobile, setMobile] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [jobId, setJobId] = useState<string>();
  const [help, setHelp] = useState(false);
  const [modelReturn, setModelReturn] = useState<Section | null>(null);
  const nav = (id: Section) => {
    if (id === "models" && section !== "models") setModelReturn(section);
    setSection(id);
    setVisited((v) => (v.includes(id) ? v : [...v, id]));
    window.history.replaceState(null, "", `#${id}`);
    setMobile(false);
    window.dispatchEvent(new Event(`inkreference:${id}-refresh`));
  };
  useEffect(() => {
    const update = () => {
      const key = window.location.hash.slice(1);
      if ([...sections.map((s) => s.id), "backup"].includes(key))
        nav(key as Section);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, [section]);
  const current = sections.find((s) => s.id === section);
  return (
    <div className={`app studio-app ${collapsed ? "sidebar-collapsed" : ""}`}>
      <a href="#workspace-content" className="skip-link">
        跳到工作区
      </a>
      {mobile && <div className="nav-shade" onClick={() => setMobile(false)} />}
      <aside
        className={`sidebar ${mobile ? "mobile-open" : ""}`}
        aria-label="工作区导航"
      >
        <button className="brand" onClick={() => nav("library")}>
          <span className="brand-mark">
            <PenLine size={21} />
          </span>
          <span>InkReference</span>
        </button>
        <div className="workspace-label">
          <span className="local-dot" />
          <span>个人工作区</span>
          <HardDrive size={13} />
        </div>
        <div className="nav-caption">创作工作台</div>
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
          >
            <FolderArchive size={18} />
            <span>备份与恢复</span>
          </button>
          <button className="sidebar-settings" onClick={() => setHelp(true)}>
            <Settings2 size={16} />
            <span>关于工作区</span>
            <span>本地</span>
          </button>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              onClick={() => setMobile(true)}
              aria-label="打开导航"
            >
              <Menu size={20} />
            </button>
            <button
              className="icon-button desktop-collapse"
              onClick={() => setCollapsed((v) => !v)}
              aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            >
              <PanelLeftClose size={17} />
            </button>
            <span>工作台</span>
            <ChevronRight size={13} />
            <strong>{current?.label || "备份与恢复"}</strong>
          </div>
          <div className="topbar-actions">
            {section === "models" && modelReturn && (
              <button className="text-button model-return" onClick={() => nav(modelReturn)}>
                返回{sections.find((s) => s.id === modelReturn)?.label || "备份与恢复"}
              </button>
            )}
            <button
              className={`button secondary model-settings-entry ${section === "models" ? "selected" : ""}`}
              onClick={() => nav("models")}
              aria-current={section === "models" ? "page" : undefined}
            >
              <SlidersHorizontal size={16} />
              模型设置
            </button>
          </div>
        </header>
        <div id="workspace-content" className="workspace-content" tabIndex={-1}>
          {visited.map((page) => (
            <div
              key={page}
              hidden={section !== page}
              className="workspace-page"
            >
              <Suspense fallback={<Busy label="正在打开工作区" />}>
                {page === "models" && modelReturn && <div className="mobile-return-bar"><button className="text-button" onClick={() => nav(modelReturn)}>返回{sections.find((s) => s.id === modelReturn)?.label || "备份与恢复"}</button></div>}
                {page === "library" ? (
                  <Library />
                ) : page === "documents" ? (
                  <Documents
                    navigate={nav}
                    onJob={(id) => {
                      setJobId(id);
                      nav("jobs");
                    }}
                  />
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
            </div>
          ))}
        </div>
      </div>
      {help && (
        <Modal title="InkReference" onClose={() => setHelp(false)}>
          <div className="modal-body">
            <p>你的本地写作参考工作台。</p>
            <div className="about-row">
              <span>素材与全文</span>
              <p>整理作品、人物、桥段，保留全文原件、清洗版本与来源证据。</p>
            </div>
            <div className="about-row">
              <span>可定制分析</span>
              <p>配置工作流、Skills 与模型，审核提取结果后再入库。</p>
            </div>
          </div>
          <div className="modal-footer">
            <button className="button primary" onClick={() => setHelp(false)}>
              开始整理
            </button>
          </div>
        </Modal>
      )}
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
      <header className="studio-heading">
        <div>
          <h1>备份与恢复</h1>
        </div>
      </header>
      <div className="backup-grid">
        <section className="surface backup-card">
          <span className="feature-icon">
            <FolderArchive size={28} />
          </span>
          <h2>完整项目备份</h2>
          <p>生成 ZIP 归档，保存当前工作区数据和文件。</p>
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
          <p>
            每张业务表生成一个
            CSV，包含全文版本、工作流、Skills、分析与调用记录。
          </p>
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
          <p>验证归档后恢复到独立目录，保留当前工作区。</p>
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
