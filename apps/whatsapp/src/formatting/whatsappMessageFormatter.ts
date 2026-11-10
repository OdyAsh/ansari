/**
 * Convert conventional markdown (what the model writes) into WhatsApp's own formatting.
 *
 * Python counterpart: `legacy/whatsapp/src/ansari_whatsapp/presenters/whatsapp_message_formatter.py`.
 *
 * That module has no tests of its own, so `tests/fixtures/pythonOracle.ts` holds what it actually returns for a table of
 * inputs, and the tests here compare against that.
 *
 * Every regular expression is copied from Python. Three things change because the two languages differ:
 * - `.` becomes `[^\n]`. Python's `.` matches `\r` and JavaScript's does not, so a Windows line ending (`\r\n`) after a
 *   header would otherwise be handled differently.
 * - `\d` becomes `\p{Nd}` with the `u` flag. Python's `\d` matches digits of any script (Arabic-Indic digits, U+0660 to
 *   U+0669, included); JavaScript's only matches 0-9. Arabic answers can number their lists with Arabic-Indic digits.
 * - Replacement text says `$1` where Python says `\1`.
 *
 * Known oddities, kept on purpose because the job here is to port behaviour, not to redesign it:
 * - Any pair of lone `*` becomes italics, even in arithmetic: "2 * 3 and 4 * 5" turns into "2 _ 3 and 4 _ 5".
 * - `***both***` becomes `**both**`: the italic step consumes nothing, then bold turns `**` into `*`, leaving two stars.
 * - The italic step can pair a `*` across lines, so a `* item` bullet list loses its bullets (`_ item`).
 */
import { getLanguageDirectionFromText } from '../utils/language';

/**
 * Convert conventional markdown to WhatsApp's markdown.
 *
 * Order matters: italic (`*x*` -> `_x_`) must run before bold (`**x**` -> `*x*`), or the new single stars would be
 * read as italics.
 */
export function formatForWhatsApp(msg: string): string {
  const direction = getLanguageDirectionFromText(msg);

  let result = convertItalicSyntax(msg);
  result = convertBoldSyntax(result);
  result = convertHeaders(result);

  // Python only formats lists when the direction is known. `getLanguageDirectionFromText` never answers 'unknown'
  // today, so this is always true; the guard is kept so the two stay identical if that ever changes.
  if (direction === 'ltr' || direction === 'rtl') {
    result = formatNestedLists(result);
  }
  return result;
}

/**
 * `(?<![*_])`  a lookbehind: the `*` must not follow another `*` or `_`
 * `\*`         a literal `*`
 * `([^*_]+?)`  the shortest run of characters that are neither `*` nor `_`, captured
 * `\*`         a literal `*`
 * `(?![*_])`   a lookahead: the `*` must not be followed by `*` or `_`
 */
const ITALIC = /(?<![*_])\*([^*_]+?)\*(?![*_])/g;

/** Convert markdown italics (`*text*`) to WhatsApp italics (`_text_`). */
export function convertItalicSyntax(text: string): string {
  return text.replace(ITALIC, '_$1_');
}

/** Convert markdown bold (`**text**`) to WhatsApp bold (`*text*`). */
export function convertBoldSyntax(text: string): string {
  return text.replaceAll('**', '*');
}

/** A header with text on the very next line: `## Title\nBody`. */
const HEADER_THEN_TEXT = /(?! )#+ \**_*([^\n]*?)\**_*\n(?!\n)/g;
/** A header followed by a blank line: `## Title\n\nBody`. */
const HEADER_THEN_BLANK_LINE = /(?! )#+ \**_*([^\n]*?)\**_*\n\n/g;

/**
 * Convert markdown headers to WhatsApp's bold+italic format, always followed by a blank line:
 * `## Title` becomes `*_Title_*`.
 */
export function convertHeaders(text: string): string {
  const withTextAfter = text.replace(HEADER_THEN_TEXT, '*_$1_*\n\n');
  return withTextAfter.replace(HEADER_THEN_BLANK_LINE, '*_$1_*\n\n');
}

const INDENT = /^(\s+)/;
const NUMBERED_ITEM = /^\s*\p{Nd}+\.\s/u;
const BULLET_ITEM = /^\s*[*-]\s/;
/** `1. ` -> `1 - `. Replaces every match on the line, as Python's `re.sub` does, not just the first. */
const NUMBERED_PREFIX = /(\s*)(\p{Nd}+)(\.) /gu;
/** `- ` or `* ` -> `-- `. Also every match on the line. */
const BULLET_PREFIX = /(\s*)[*-] /g;

/**
 * Reformat only nested list items (indented ones) for WhatsApp, which renders them badly:
 * - nested numbered item `  1. Item` becomes `  1 - Item`
 * - nested bullet `  - Item` or `  * Item` becomes `  -- Item`
 *
 * Top-level lists keep their original formatting.
 *
 * Python also tracked an "inside a nested section" flag here, which was set and cleared but never changed the output,
 * so it is not ported.
 */
export function formatNestedLists(text: string): string {
  return text
    .split('\n')
    .map((line) => {
      // An indent only counts on a line with something on it
      const indent = line.trim() ? INDENT.exec(line) : null;
      const currentIndent = indent ? indent[1].length : 0;
      if (currentIndent === 0) return line;

      if (NUMBERED_ITEM.test(line)) return line.replace(NUMBERED_PREFIX, '$1$2 - ');
      if (BULLET_ITEM.test(line)) return line.replace(BULLET_PREFIX, '$1-- ');
      return line;
    })
    .join('\n');
}
