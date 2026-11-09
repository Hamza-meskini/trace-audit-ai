/**
 * Whitespace-insensitive text matching for cited evidence spans.
 *
 * Citations are stored as plain text; extracted page blocks may wrap lines or
 * collapse table spacing differently, so any run of whitespace in the cited
 * text matches any run of whitespace in the page text.
 */

const MIN_NEEDLE_LENGTH = 4;

export function citationPattern(needle: string | null | undefined): RegExp | null {
  const cleaned = (needle || "").replace(/\s+/g, " ").trim();
  if (cleaned.length < MIN_NEEDLE_LENGTH) return null;
  const escaped = cleaned
    .split(" ")
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("\\s+");
  return new RegExp(escaped, "gi");
}

export interface TextSegment {
  text: string;
  match: boolean;
}

/** Split `text` into alternating plain / matched segments. */
export function splitByCitation(text: string, pattern: RegExp | null): TextSegment[] {
  if (!pattern || !text) return [{ text, match: false }];
  const segments: TextSegment[] = [];
  let last = 0;
  for (const found of text.matchAll(new RegExp(pattern.source, pattern.flags))) {
    const start = found.index ?? 0;
    if (!found[0]) continue;
    if (start > last) segments.push({ text: text.slice(last, start), match: false });
    segments.push({ text: found[0], match: true });
    last = start + found[0].length;
  }
  if (last < text.length) segments.push({ text: text.slice(last), match: false });
  return segments.length ? segments : [{ text, match: false }];
}

export function containsCitation(text: string, pattern: RegExp | null): boolean {
  return !!pattern && new RegExp(pattern.source, "i").test(text);
}
