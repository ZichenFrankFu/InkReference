import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  FileText,
  Play,
  RefreshCw,
  Square,
  Upload,
} from "lucide-react";
import { download } from "./api";
import {
  studio,
  send,
  normalizeList,
  type List,
  type DocumentMeta,
} from "./studio";
import {
  Busy,
  Empty,
  Notice,
  Progress,
  SelectField,
  Status,
  dateLabel,
  messageOf,
} from "./ui";
type Conversion = {
  id: string;
  filename: string;
  kind: "epub" | "pdf";
  status: string;
  stage: string;
  progress: number | null;
  completed: number | null;
  total: number | null;
  options: { tier?: string; ocr_mode?: string };
  error?: string;
  created_at: string;
  updated_at: string;
  outputs: { markdown: boolean; text: boolean };
  imported_document_id?: string;
};
export default function Conversions({
  onImported,
}: {
  onImported: (id: string) => void;
}) {
  const [items, setItems] = useState<Conversion[]>([]);
  const [selected, setSelected] = useState<Conversion | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [tier, setTier] = useState("flash");
  const [ocr, setOcr] = useState("txt");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const input = useRef<HTMLInputElement>(null);
  const current = useRef(selected);
  current.current = selected;
  const load = useCallback(async () => {
    try {
      setItems(normalizeList(await studio<List<Conversion>>("/conversions")));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      const c = current.current;
      if (c && ["queued", "running", "cancelling"].includes(c.status)) {
        void studio<Conversion>(`/conversions/${c.id}`)
          .then((result) => {
            setSelected(result);
            setItems((items) =>
              items.map((i) => (i.id === result.id ? result : i)),
            );
          })
          .catch((e) => setError(messageOf(e)));
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, []);
  const create = async () => {
    if (!file) return;
    setBusy("create");
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append(
        "options",
        JSON.stringify(
          file.name.toLowerCase().endsWith(".pdf")
            ? { tier, ocr_mode: ocr }
            : {},
        ),
      );
      let result = await studio<Conversion>("/conversions", {
        method: "POST",
        body: form,
      });
      if (result.status === "queued")
        result = await send<Conversion>(`/conversions/${result.id}/start`, {});
      setSelected(result);
      setFile(null);
      if (input.current) input.current.value = "";
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const action = async (kind: "cancel" | "retry" | "start") => {
    if (!selected) return;
    setBusy(kind);
    setError("");
    try {
      const result = await send<Conversion>(
        `/conversions/${selected.id}/${kind}`,
        {},
      );
      setSelected(result);
      await load();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const getFile = async (format: "md" | "txt") => {
    if (!selected) return;
    setBusy(format);
    setError("");
    try {
      await download(
        `/studio/conversions/${selected.id}/download?format=${format}`,
        selected.filename.replace(/\.[^.]+$/, `.${format}`),
      );
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const importText = async () => {
    if (!selected) return;
    setBusy("import");
    setError("");
    try {
      const result = await send<DocumentMeta>(
        `/conversions/${selected.id}/import`,
        {},
      );
      await load();
      onImported(result.id);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy("");
    }
  };
  const running =
    selected && ["running", "queued", "cancelling"].includes(selected.status);
  return (
    <>
      <Notice error>{error}</Notice>
      <div className="conversion-layout">
        <section className="surface conversion-upload">
          <div className="surface-heading">
            <h2>转换新文件</h2>
          </div>
          <div className="conversion-form">
            <input
              ref={input}
              type="file"
              accept=".epub,.pdf"
              hidden
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <button
              className="upload-panel"
              onClick={() => input.current?.click()}
              disabled={!!busy}
            >
              <Upload size={28} />
              <strong>{file?.name || "选择 EPUB 或 PDF"}</strong>
              <span>原件 → Markdown → TXT</span>
            </button>
            {file?.name.toLowerCase().endsWith(".pdf") && (
              <>
                <SelectField
                  label="PDF 处理模式"
                  value={tier}
                  onChange={(v) => {
                    setTier(v);
                    if (v === "flash") setOcr("txt");
                  }}
                >
                  <option value="flash">Flash · 原生文字层</option>
                  <option value="standard">Standard · 完整解析</option>
                </SelectField>
                <SelectField
                  label="文字识别"
                  value={ocr}
                  onChange={setOcr}
                  disabled={tier === "flash"}
                >
                  <option value="txt">读取文本层</option>
                  <option value="ocr">OCR 识别扫描页</option>
                </SelectField>
              </>
            )}
            <div className="conversion-path">
              <span>EPUB / PDF</span>
              <ArrowRight size={12} />
              <span>Markdown</span>
              <ArrowRight size={12} />
              <span>TXT</span>
            </div>
            <button
              className="button primary"
              disabled={!file || !!busy}
              onClick={() => void create()}
            >
              <Play size={15} />
              开始转换
            </button>
          </div>
        </section>
        <section className="surface conversion-workspace">
          <div className="surface-heading">
            <h2>转换任务</h2>
            <span>{items.length}</span>
            <button
              className="icon-button"
              onClick={() => void load()}
              aria-label="刷新转换任务"
            >
              <RefreshCw size={15} />
            </button>
          </div>
          {loading ? (
            <Busy label="正在读取转换任务" />
          ) : !items.length ? (
            <Empty
              icon={FileText}
              title="把不同格式，变成可整理的文本"
              description="EPUB 提取章节；PDF 通过本地 MinerU 解析。"
            />
          ) : (
            <div className="conversion-jobs">
              {items.map((item) => (
                <button
                  className={`conversion-item ${selected?.id === item.id ? "active" : ""}`}
                  key={item.id}
                  onClick={() => setSelected(item)}
                >
                  <span className="document-file-icon">
                    {item.kind === "epub" ? (
                      <BookOpen size={20} />
                    ) : (
                      <FileText size={20} />
                    )}
                  </span>
                  <span>
                    <strong>{item.filename}</strong>
                    <small>
                      {item.kind.toUpperCase()} · {dateLabel(item.created_at)}
                    </small>
                  </span>
                  <Status value={item.status} />
                </button>
              ))}
            </div>
          )}
          {selected && (
            <div className="conversion-detail">
              <div className="conversion-detail-heading">
                <div>
                  <span className="eyebrow">
                    {selected.kind.toUpperCase()} CONVERSION
                  </span>
                  <h3>{selected.filename}</h3>
                </div>
                {running ? (
                  <button
                    className="button secondary"
                    onClick={() => void action("cancel")}
                    disabled={!!busy}
                  >
                    <Square size={13} />
                    取消转换
                  </button>
                ) : ["failed", "cancelled", "interrupted"].includes(
                    selected.status,
                  ) ? (
                  <button
                    className="button secondary"
                    onClick={() => void action("retry")}
                    disabled={!!busy}
                  >
                    <RefreshCw size={14} />
                    重试
                  </button>
                ) : null}
              </div>
              <Progress
                completed={selected.completed ?? undefined}
                total={selected.total ?? undefined}
                stage={
                  ({ queued: "等待转换", reading: "读取原件", epub_markdown: "生成 Markdown", markdown: "生成 Markdown", mineru: "MinerU 解析中", pdf_markdown: "MinerU 解析中", plain_text: "生成纯文本", text: "生成纯文本", completed: "转换完成", failed: "转换失败", cancelled: "已取消", interrupted: "已中断" } as Record<string, string>)[selected.stage] || "正在转换"
                }
                busy={!!running}
              />
              {selected.error && <Notice error>{selected.error}</Notice>}
              {selected.status === "completed" && (
                <div className="conversion-outputs">
                  <div>
                    <CheckCircle2 size={19} />
                    <strong>转换完成</strong>
                  </div>
                  <div className="inline-actions">
                    <button
                      className="button secondary"
                      onClick={() => void getFile("md")}
                      disabled={!!busy || !selected.outputs.markdown}
                    >
                      <ArrowDownToLine size={15} />
                      Markdown
                    </button>
                    <button
                      className="button secondary"
                      onClick={() => void getFile("txt")}
                      disabled={!!busy || !selected.outputs.text}
                    >
                      <ArrowDownToLine size={15} />
                      TXT
                    </button>
                    <button
                      className="button primary"
                      onClick={() =>
                        selected.imported_document_id
                          ? onImported(selected.imported_document_id)
                          : void importText()
                      }
                      disabled={!!busy || !selected.outputs.text}
                    >
                      {selected.imported_document_id
                        ? "打开全文"
                        : "加入全文库"}
                      <ArrowRight size={14} />
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
          {busy && (
            <Busy
              label={
                {
                  create: "正在上传并创建转换",
                  cancel: "正在请求取消",
                  retry: "正在重新启动转换",
                  import: "正在把 TXT 导入全文库",
                  md: "正在准备 Markdown",
                  txt: "正在准备 TXT",
                  start: "正在启动转换",
                }[busy]
              }
            />
          )}
        </section>
      </div>
    </>
  );
}
