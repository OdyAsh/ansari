/**
 * Markdown to WhatsApp formatting. Python has no test for the formatter, so every expected value here is what the
 * Python formatter returned (`tests/fixtures/pythonOracle.ts`), including its oddities. The extra tests below name the
 * ones that surprise.
 */
import { describe, expect, it } from 'vitest';
import {
  convertBoldSyntax,
  convertHeaders,
  convertItalicSyntax,
  formatForWhatsApp,
  formatNestedLists,
} from '../src/formatting/whatsappMessageFormatter';
import {
  BOLD_CASES,
  EMPTY_TEXT,
  FORMATTER_CASES,
  HEADER_CASES,
  ITALIC_CASES,
  NESTED_LIST_CASES,
} from './fixtures/pythonOracle';

describe('formatForWhatsApp (whole pipeline)', () => {
  it.each(FORMATTER_CASES)('%j', (input, expected) => {
    expect(formatForWhatsApp(input)).toBe(expected);
  });

  it('formats a typical answer end to end', () => {
    const answer = '## Ruling\n\nThe **majority** hold it is *recommended*.\n\n1. First\n   - detail a\n   - detail b\n2. Second';
    expect(formatForWhatsApp(answer)).toBe(
      '*_Ruling_*\n\nThe *majority* hold it is _recommended_.\n\n1. First\n   -- detail a\n   -- detail b\n2. Second',
    );
  });

  // Python raises ZeroDivisionError on empty text (inside the direction check). Callers return before formatting an
  // empty answer, so this is unreachable; the port answers with the empty text.
  it('returns empty text for empty text where Python raised ZeroDivisionError', () => {
    expect(EMPTY_TEXT.format).toBe('ZeroDivisionError');
    expect(formatForWhatsApp('')).toBe('');
  });
});

describe('the steps on their own', () => {
  it.each(ITALIC_CASES)('italic %j', (input, expected) => {
    expect(convertItalicSyntax(input)).toBe(expected);
  });
  it.each(BOLD_CASES)('bold %j', (input, expected) => {
    expect(convertBoldSyntax(input)).toBe(expected);
  });
  it.each(HEADER_CASES)('headers %j', (input, expected) => {
    expect(convertHeaders(input)).toBe(expected);
  });
  it.each(NESTED_LIST_CASES)('nested lists %j', (input, expected) => {
    expect(formatNestedLists(input)).toBe(expected);
  });
});

// These behaviours look like bugs. They are Python's, and the port keeps them until the owner decides otherwise.
describe('behaviour kept from Python on purpose', () => {
  it('turns arithmetic stars into italics', () => {
    expect(formatForWhatsApp('2 * 3 = 6 and 4 * 5 = 20')).toBe('2 _ 3 = 6 and 4 _ 5 = 20');
  });

  it('turns *** into ** (a stray double star)', () => {
    expect(formatForWhatsApp('***both***')).toBe('**both**');
  });

  it('loses the bullets of a * list', () => {
    expect(formatForWhatsApp('* a\n* b')).toBe('_ a\n_ b');
  });

  it('puts a carriage return inside the header when the line ends in \\r\\n', () => {
    expect(formatForWhatsApp('## Header\r\n\r\nbody')).toBe('*_Header\r_*\n\n\r\nbody');
  });
});

describe('where JavaScript and Python differ, and the port follows Python', () => {
  it('numbers nested items written with Arabic-Indic digits (JavaScript \\d would miss them)', () => {
    expect(/\d/.test('\u0661')).toBe(false);
    expect(formatNestedLists('  \u0661. item')).toBe('  \u0661 - item');
  });

  it('rewrites every list marker on a line, not just the first', () => {
    expect(formatNestedLists('  - a - b')).toBe('  -- a -- b');
  });

  it('treats a no-break space as indentation, like Python', () => {
    expect(formatNestedLists('\u00a0\u00a0- a')).toBe('\u00a0\u00a0-- a');
  });
});
