import { forwardRef, useImperativeHandle, useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import { preserveTextareaEdit, textareaText } from "./chapterText";

export type TextHighlight = { id: string; start: number; end: number; label: string; kind: string; selected: boolean; removable: boolean };
export type ChapterEditorHandle = { reveal: (start: number, end: number) => void; focus: () => void };
type Props = { value: string; highlights: TextHighlight[]; readOnly: boolean; placeholder?: string; onChange: (value: string) => void };

function markedNodes(points: string[], mapped: TextHighlight[]) {
  const nodes: ReactNode[] = [];
  const starts = new Map<number, typeof mapped>(), ends = new Map<number, string[]>();
  for (const mark of mapped) {
    const start = Math.max(0, mark.start), end = Math.min(points.length, mark.end);
    if (start >= end) continue;
    starts.set(start, [...(starts.get(start) || []), mark]);
    ends.set(end, [...(ends.get(end) || []), mark.id]);
  }
  const boundaries = [...new Set([0, points.length, ...starts.keys(), ...ends.keys()])].sort((a, b) => a - b);
  const active = new Map<string, (typeof mapped)[number]>();
  for (let i = 0; i < boundaries.length - 1; i++) {
    const start = boundaries[i], end = boundaries[i + 1];
    for (const id of ends.get(start) || []) active.delete(id);
    for (const item of starts.get(start) || []) active.set(item.id, item);
    if (start >= end) continue;
    const mark = [...active.values()].sort((a, b) => Number(a.removable) - Number(b.removable))[0];
    const text = points.slice(start, end).join("");
    if (!mark) nodes.push(text);
    else nodes.push(<mark key={`${mark.id}-${start}`} data-marker={mark.id} data-offset={start} className={`chapter-input-mark ${mark.removable ? mark.selected ? "removing" : "keeping" : "diagnostic"} ${mark.kind} ${/^[\p{Cf}\p{Cc}]+$/u.test(text) ? "hidden-character" : ""}`} title={mark.label}>{text}</mark>);
  }
  return nodes;
}

export const ChapterTitleEditor = forwardRef<HTMLInputElement, Props>(function ChapterTitleEditor({ value, highlights, readOnly, placeholder, onChange }, forwardedRef) {
  const input = useRef<HTMLInputElement>(null);
  const background = useRef<HTMLDivElement>(null);
  const sync = () => { if (background.current && input.current) background.current.style.transform = `translateX(${-input.current.scrollLeft}px)`; };
  useImperativeHandle(forwardedRef, () => input.current!);
  useLayoutEffect(sync, [value]);
  return <div className="chapter-title-editor">
    <div className="chapter-title-background" aria-hidden="true"><div ref={background}>{markedNodes(Array.from(value), highlights)}</div></div>
    <input ref={input} className="chapter-title-input" aria-label="章节标题" maxLength={80} value={value} readOnly={readOnly} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} onScroll={sync} spellCheck={false} autoComplete="off" />
  </div>;
});

// Native text input keeps IME composition, caret movement and undo intact. The
// background layer only supplies marks, so the reader sees one editable text.
const ChapterTextEditor = forwardRef<ChapterEditorHandle, Props>(function ChapterTextEditor({ value, highlights, readOnly, placeholder, onChange }, ref) {
  const input = useRef<HTMLTextAreaElement>(null);
  const background = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const normalized = useMemo(() => textareaText(value), [value]);
  const points = useMemo(() => Array.from(normalized.value), [normalized.value]);
  const mapped = highlights.map((mark) => ({ ...mark, start: normalized.sourceToDisplay[Math.min(mark.start, normalized.sourceToDisplay.length - 1)] || 0, end: normalized.sourceToDisplay[Math.min(mark.end, normalized.sourceToDisplay.length - 1)] || 0 }));
  const nodes = markedNodes(points, mapped);
  const sync = () => {
    if (!input.current || !content.current || !background.current) return;
    background.current.style.width = `${input.current.clientWidth}px`;
    content.current.style.transform = `translate(${-input.current.scrollLeft}px, ${-input.current.scrollTop}px)`;
  };
  useLayoutEffect(() => {
    sync();
    const observer = new ResizeObserver(sync);
    if (input.current) observer.observe(input.current);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(sync, [value]);
  useImperativeHandle(ref, () => ({
    focus: () => input.current?.focus(),
    reveal: (start, end) => {
      const target = input.current;
      if (!target) return;
      const a = normalized.sourceToDisplay[Math.max(0, Math.min(normalized.sourceToDisplay.length - 1, start))] || 0, b = normalized.sourceToDisplay[Math.max(0, Math.min(normalized.sourceToDisplay.length - 1, end))] || a;
      target.focus({ preventScroll: true });
      target.setSelectionRange(points.slice(0, a).join("").length, points.slice(0, b).join("").length);
      const marker = Array.from(content.current?.querySelectorAll<HTMLElement>("[data-offset]") || []).find((node) => Number(node.dataset.offset) === a);
      if (marker) target.scrollTop = Math.max(0, marker.offsetTop - target.clientHeight / 3);
      else {
        const font = getComputedStyle(target), lineHeight = parseFloat(font.lineHeight) || 32;
        const before = points.slice(0, a).join(""), columns = Math.max(1, Math.floor(target.clientWidth / (parseFloat(font.fontSize) || 15)));
        target.scrollTop = Math.max(0, (before.split("\n").reduce((total, line) => total + Math.max(1, Math.ceil(Array.from(line).length / columns)), 0) - 1) * lineHeight - target.clientHeight / 3);
      }
      sync();
    },
  }), [value]);
  return <div className={`chapter-live-editor ${readOnly ? "readonly" : ""}`}>
    <div ref={background} className="chapter-input-background" aria-hidden="true"><div ref={content} className="chapter-input-background-content">{nodes}{"\n"}</div></div>
    <textarea ref={input} className="chapter-input-text" aria-label="章节正文" placeholder={placeholder} value={normalized.value} readOnly={readOnly} onChange={(event) => onChange(preserveTextareaEdit(value, event.target.value))} onScroll={sync} spellCheck={false} wrap="soft" autoCapitalize="off" autoComplete="off" autoCorrect="off" />
  </div>;
});
export default ChapterTextEditor;
