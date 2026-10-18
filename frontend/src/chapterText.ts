/** Textareas display CRLF/CR as LF; retain source offsets and untouched bytes. */
export function textareaText(source: string) {
  const rawBoundaries = [0];
  const chars: string[] = [];
  let raw = 0;
  while (raw < source.length) {
    if (source[raw] === "\r") {
      raw += source[raw + 1] === "\n" ? 2 : 1;
      chars.push("\n");
    } else chars.push(source[raw++]);
    rawBoundaries.push(raw);
  }
  const value = chars.join("");
  const sourcePoints = Array.from(source), sourceToDisplay = [0];
  let display = 0;
  for (let i = 0; i < sourcePoints.length; i++) {
    if (sourcePoints[i] === "\r" && sourcePoints[i + 1] === "\n") {
      sourceToDisplay.push(display);
      sourceToDisplay.push(++display);
      i++;
    } else sourceToDisplay.push(++display);
  }
  return { value, rawBoundaries, sourceToDisplay };
}

export function preserveTextareaEdit(source: string, next: string): string {
  const current = textareaText(source);
  if (current.value === next) return source;
  let before = 0;
  while (before < current.value.length && before < next.length && current.value[before] === next[before]) before++;
  let oldEnd = current.value.length, newEnd = next.length;
  while (oldEnd > before && newEnd > before && current.value[oldEnd - 1] === next[newEnd - 1]) { oldEnd--; newEnd--; }
  const neighboring = source.slice(Math.max(0, current.rawBoundaries[before] - 400), current.rawBoundaries[Math.min(current.value.length, oldEnd + 400)]);
  const newline = neighboring.includes("\r\n") ? "\r\n" : neighboring.includes("\r") && !neighboring.includes("\n") ? "\r" : "\n";
  const inserted = next.slice(before, newEnd).replace(/\n/g, newline);
  return source.slice(0, current.rawBoundaries[before]) + inserted + source.slice(current.rawBoundaries[oldEnd]);
}
