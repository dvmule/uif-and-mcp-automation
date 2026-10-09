/**
 * The pure text operations behind the Code view: how a body is formatted for
 * display, and where a query matches it.
 *
 * They lived in `SchemaViewer.tsx` beside the component. Fast Refresh cannot
 * hot-swap a module that exports both a component and other values — it
 * remounts the whole module instead, losing editor state — which is what
 * `react-refresh/only-export-components` was reporting. They are also the
 * pieces the schema page needs WITHOUT mounting the editor, so a module of
 * their own is where they belonged anyway.
 */
import { getMonacoLanguage } from './utils';

/**
 * The body exactly as the editor will show it.
 *
 * Exported because the page's match rail, its "n of m" count and its stepper
 * were all computed from the RAW body while the editor rendered this. For a
 * pretty-printed body the two strings are identical and nothing showed; for a
 * minified one they are not. Measured on a two-field record: the raw body
 * reports one match at L1, the rendered body two, at L6 and L10 — so the rail
 * pointed at a line that did not hold the match, the count was wrong, and
 * `activeMatch` indexed a one-item array against the editor's two.
 *
 * Bodies published through this app are re-serialised with indent=2 by the
 * BFF, which is why every artifact in the live registry is pretty-printed
 * today. A body registered straight against Apicurio is stored as sent — the
 * seed script's own `json.dumps` has no indent — so this is reachable, not
 * theoretical.
 */
export function displayedContent(schemaType: string, content: string): string {
  return formatContent(content, getMonacoLanguage(schemaType ?? '', content));
}

function formatContent(content: string, language: string): string {
  if (language === 'json') {
    try {
      return JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      return content;
    }
  }
  return content;
}

/** Lines of `content` containing `query`, 1-indexed, with the hit column. */
export interface CodeMatch {
  line: number;
  column: number;
  /** The whole line, trimmed — what the match rail shows beside `L11`. */
  text: string;
}

export function findCodeMatches(content: string, query: string): CodeMatch[] {
  if (!query) return [];
  const needle = query.toLowerCase();
  const out: CodeMatch[] = [];
  content.split('\n').forEach((text, i) => {
    const col = text.toLowerCase().indexOf(needle);
    if (col !== -1) out.push({ line: i + 1, column: col + 1, text: text.trim() });
  });
  return out;
}
