import { useId, useState, type ReactNode } from "react";
import { BookOpen, ChevronLeft, ChevronRight, Search } from "lucide-react";
import {
  clueStatuses, eventTypes, formatNarrativeOrder, settingTypes,
  type ChronicleClue, type ChronicleResult, type ViewSource,
} from "./chronicle-types";

export function Original({ lines, title, onView }: { lines: number[]; title: string; onView: ViewSource }) {
  if (!lines.length) return null;
  return <button className="text-button chronicle-original" onClick={() => onView(lines, title)}><BookOpen size={14} />查看原文</button>;
}

function Table({ headers, children, wide = false }: { headers: string[]; children: ReactNode; wide?: boolean }) {
  return <div className="chronicle-table-region" role="region" aria-label={`${headers[1]}记录`} tabIndex={0}>
    <table className={`chronicle-table${wide ? " chronicle-table-wide" : ""}`}>
      <thead><tr>{headers.map(title => <th scope="col" key={title}>{title}</th>)}</tr></thead>
      <tbody>{children}</tbody>
    </table>
  </div>;
}

function Rows<T>({ items, empty, children }: { items: T[]; empty: string; children: (page: T[]) => ReactNode }) {
  const size = 50;
  const [page, setPage] = useState(0);
  if (!items.length) return <div className="ref-empty">{empty}</div>;
  const pages = Math.ceil(items.length / size);
  const current = Math.min(page, pages - 1);
  return <>
    {children(items.slice(current * size, (current + 1) * size))}
    {pages > 1 && <div className="chronicle-pagination"><span>{current + 1} / {pages} 页 · {items.length} 条</span><div className="inline-actions">
      <button className="button secondary" onClick={() => setPage(current - 1)} disabled={!current}><ChevronLeft size={14} />上一页</button>
      <button className="button secondary" onClick={() => setPage(current + 1)} disabled={current + 1 >= pages}>下一页<ChevronRight size={14} /></button>
    </div></div>}
  </>;
}

function Filter({ label, value, onChange, options, all = "全部" }: { label: string; value: string; onChange: (v: string) => void; options: readonly (string | readonly [string, string])[]; all?: string }) {
  return <label className="chronicle-filter"><span>{label}</span><select aria-label={label} value={value} onChange={e => onChange(e.target.value)}>
    <option value="">{all}</option>{options.map(option => typeof option === "string" ? <option key={option} value={option}>{option}</option> : <option key={option[0]} value={option[0]}>{option[1]}</option>)}
  </select></label>;
}

export function ChronicleEvents({ result, storyline, onStoryline, onView }: {
  result: ChronicleResult; storyline: string; onStoryline: (id: string) => void; onView: ViewSource;
}) {
  const [type, setType] = useState("");
  const [query, setQuery] = useState("");
  const titles = new Map([...result.storylines, ...result.foreshadowing].map(line => [line.id, line.title]));
  const clues = new Map(result.foreshadowing.map(clue => [clue.id, clue]));
  const labelLine = (id: string, eventId: string) => {
    const clue = clues.get(id);
    const roles = [];
    if (clue?.plant.event_id === eventId) roles.push("埋设");
    if (clue?.return?.event_id === eventId) roles.push("收回");
    return `${titles.get(id) || id}${roles.length ? `（${roles.join("、")}）` : ""}`;
  };
  const search = query.trim().toLocaleLowerCase();
  const selectedLine = titles.has(storyline) ? storyline : "";
  const matched = result.events.map((event, order) => ({ event, order })).filter(({ event, order }) => {
    return (!type || event.type === type) && (!selectedLine || event.storyline_ids.includes(selectedLine)) &&
      (!search || [formatNarrativeOrder(order), event.title, event.description, event.location, event.story_time].some(text => text.toLocaleLowerCase().includes(search)));
  });
  return <>
    <div className="chronicle-filters">
      <label className="chronicle-search"><Search size={15} /><input type="search" aria-label="搜索事件" placeholder="搜索事件" value={query} onChange={e => setQuery(e.target.value)} /></label>
      <Filter label="类型" options={eventTypes} value={type} onChange={setType} />
      <Filter label="故事线" options={[...titles]} value={selectedLine} onChange={onStoryline} />
    </div>
    <Rows key={`${type}/${selectedLine}/${query}`} items={matched} empty="没有符合条件的事件">{items => <Table wide headers={["叙述序号", "标题", "类型", "故事中时间", "地点", "事件描述", "所属故事线", "定位依据"]}>
      {items.map(({ event, order }) => <tr key={event.id}>
        <td className="chronicle-order">{formatNarrativeOrder(order)}</td><th scope="row">{event.title}</th>
        <td><span className="chronicle-tag" data-value={event.type}>{event.type}</span></td>
        <td data-label="故事中时间">{event.story_time}</td><td data-label="地点">{event.location}</td>
        <td data-label="事件描述">{event.description}</td>
        <td data-label="所属故事线"><ul className="chronicle-lines">{event.storyline_ids.map(id => <li key={id}>{labelLine(id, event.id)}</li>)}</ul></td>
        <td data-label="定位依据">{event.basis}<Original lines={event.source_lines} title={event.title} onView={onView} /></td>
      </tr>)}
    </Table>}</Rows>
  </>;
}

export function ChronicleStorylines({ result, showEvents }: { result: ChronicleResult; showEvents: (id: string) => void }) {
  const names = new Map(result.characters.map(c => [c.id, c.name]));
  return <Rows items={result.storylines} empty="没有故事线">{items => <div className="chronicle-storylines">
    {items.map(line => {
      const clues = result.foreshadowing.filter(c => c.storyline_id === line.id);
      const eventCount = result.events.filter(event => event.storyline_ids.includes(line.id)).length;
      return <article className="chronicle-storyline" key={line.id}>
        <header><h4>{line.title}</h4>{eventCount > 0 && <button className="text-button" onClick={() => showEvents(line.id)}>查看 {eventCount} 个事件</button>}</header>
        <dl className="chronicle-phases">
          {line.character_ids.length > 0 && <div><dt>相关角色</dt><dd>{line.character_ids.map(id => names.get(id) || id).join("、")}</dd></div>}
          {([['起', line.beginning], ['承', line.development], ['转', line.turn], ['合', line.conclusion]] as const).map(([key, phase]) => <div key={key}><dt>{key}</dt><dd>{phase}</dd></div>)}
          {clues.length > 0 && <div><dt>伏笔子线</dt><dd>{clues.map(c => c.title).join("、")}</dd></div>}
        </dl>
      </article>;
    })}
  </div>}</Rows>;
}

export function ChronicleCharacters({ result, onView }: { result: ChronicleResult; onView: ViewSource }) {
  return <Rows items={result.characters} empty="没有人物条目">{items => <Table headers={["姓名或代号", "别名", "身份", "身份依据"]}>
    {items.map(c => <tr key={c.id}><th scope="row">{c.name}</th><td>{c.aliases.join("、")}</td><td>{c.identity}</td><td>{c.basis}<Original lines={c.source_lines} title={c.name} onView={onView} /></td></tr>)}
  </Table>}</Rows>;
}

export function ChronicleRelationships({ result, onView }: { result: ChronicleResult; onView: ViewSource }) {
  const [first, setFirst] = useState("");
  const [second, setSecond] = useState("");
  const [cut, setCut] = useState("");
  const label = useId();
  const names = new Map(result.characters.map(c => [c.id, c.name]));
  const items = result.relationships.filter(r => {
    const people = [r.character_a_id, r.character_b_id];
    return (!first || people.includes(first)) && (!second || people.includes(second)) &&
      (!Number(cut) || (r.narrative_order <= Number(cut) && (r.end_order == null || r.end_order >= Number(cut))));
  });
  return <>
    <div className="chronicle-filters">
      <Filter label="人物 A" options={[...names].filter(([id]) => id !== second)} value={first} onChange={setFirst} />
      <Filter label="人物 B" options={[...names].filter(([id]) => id !== first)} value={second} onChange={setSecond} />
      <label className="chronicle-filter"><span>截至原文行号</span><input type="number" min={1} aria-label="截至原文行号" list={label} placeholder="全书" value={cut} onChange={e => { const next = e.target.value; if (!next || /^[1-9]\d*$/.test(next)) setCut(next); }} /></label>
      <datalist id={label}>{[...new Set(result.relationships.map(r => r.narrative_order))].map(line => <option key={line} value={line}>L{line}</option>)}</datalist>
    </div>
    <Rows key={`${first}/${second}/${cut}`} items={items} empty="没有符合条件的关系记录">{records => <Table headers={["人物 A", "人物 B", "叙述顺序", "关系", "依据"]}>
      {records.map((r, index) => {
        const shownSources = r.source_lines.filter(line => !Number(cut) || line <= Number(cut));
        const showBasis = shownSources.length === r.source_lines.length;
        return <tr key={`${r.character_a_id}/${r.character_b_id}/${r.narrative_order}/${r.relation}/${index}`}>
          <th scope="row">{names.get(r.character_a_id)}</th><td>{names.get(r.character_b_id)}</td>
          <td className="chronicle-order">L{r.narrative_order}{r.end_order && (!Number(cut) || r.end_order <= Number(cut)) ? ` 至 L${r.end_order}` : ""}</td><td>{r.relation}</td>
          <td>{showBasis ? r.basis : ""}<Original lines={shownSources} title={r.relation} onView={onView} /></td>
        </tr>;
      })}
    </Table>}</Rows>
  </>;
}

export function ChronicleSettings({ result, onView }: { result: ChronicleResult; onView: ViewSource }) {
  const [category, setCategory] = useState("");
  return <><div className="chronicle-filters"><Filter label="类别" options={settingTypes} value={category} onChange={setCategory} /></div>
    <Rows key={category} items={result.settings.filter(s => !category || s.category === category)} empty="没有符合条件的设定">{items => <Table headers={["名称", "类别", "说明"]}>
      {items.map(s => <tr key={s.id}><th scope="row">{s.name}</th><td>{s.category}</td><td>{s.description}<Original lines={s.source_lines} title={s.name} onView={onView} /></td></tr>)}
    </Table>}</Rows>
  </>;
}

export function ChronicleClues({ result, onView }: { result: ChronicleResult; onView: ViewSource }) {
  const [status, setStatus] = useState("");
  const evidence = (endpoint: ChronicleClue["return"], title: string) => endpoint && <>{endpoint.basis}<Original lines={endpoint.source_lines} title={title} onView={onView} /></>;
  return <><div className="chronicle-filters"><Filter label="状态" options={clueStatuses} value={status} onChange={setStatus} /></div>
    <Rows key={status} items={result.foreshadowing.filter(clue => !status || clue.status === status)} empty="没有符合条件的伏笔">{items => <Table headers={["编号", "条目", "状态", "埋设", "收回"]}>
      {items.map(c => <tr key={c.id}><td className="chronicle-order">{c.id}</td><th scope="row">{c.title}</th><td><span className="chronicle-tag" data-value={c.status}>{c.status}</span></td><td>{evidence(c.plant, c.title)}</td><td>{evidence(c.return, c.title)}</td></tr>)}
    </Table>}</Rows>
  </>;
}
