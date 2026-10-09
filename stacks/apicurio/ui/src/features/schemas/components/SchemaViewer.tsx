import { useEffect, useRef, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import Editor from '@monaco-editor/react';
import type { OnMount, BeforeMount } from '@monaco-editor/react';
import type { editor as MonacoEditor } from 'monaco-editor';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { getMonacoLanguage } from '../utils';
import { displayedContent, findCodeMatches } from '../code-view';

/**
 * 4b — the schema body as code.
 *
 * The three token colours are a custom Monaco theme rather than the stock `vs`
 * palette: object keys Amethyst, string values the fuchsia shade, structure
 * Pearson Purple. All three clear AA on white, which the default theme's blues
 * and greens were never checked for.
 *
 * Monaco's own find widget is suppressed so one search field serves all three
 * views — a second find box that only worked in Code view, bound to the same
 * key, is worse than no keyboard search at all.
 *
 * One documented deviation from the design: matched lines take the amber tint
 * across both the content and the gutter, but their gutter *number* stays the
 * muted purple rather than turning `#7F6701`. Monaco exposes no per-line
 * styling for line numbers — only a global colour and an active-line colour —
 * so the alternative was replacing its gutter, which would also have cost the
 * language support that earns Monaco its place here for YAML, XSD and GraphQL
 * bodies. The match rail to the right lists every hit by line number, so the
 * information is not lost.
 */

export const PEARSON_MONACO_THEME = 'pearson-registry';


const defineTheme: BeforeMount = (monaco) => {
  monaco.editor.defineTheme(PEARSON_MONACO_THEME, {
    base: 'vs',
    inherit: true,
    rules: [
      // Monaco's JSON tokenizer distinguishes a key from a value, which is
      // exactly the distinction the design's palette draws.
      { token: 'string.key.json', foreground: '512EAB' },
      { token: 'string.value.json', foreground: '7B1571' },
      { token: 'string', foreground: '7B1571' },
      { token: 'string.yaml', foreground: '7B1571' },
      { token: 'type.yaml', foreground: '512EAB' },
      { token: 'attribute.name', foreground: '512EAB' },
      { token: 'attribute.value', foreground: '7B1571' },
      { token: 'tag', foreground: '512EAB' },
      { token: 'key', foreground: '512EAB' },
      { token: 'number', foreground: '7B1571' },
      { token: 'keyword', foreground: '7B1571' },
      { token: 'comment', foreground: '5A5377' },
      { token: 'delimiter', foreground: '0D004D' },
      { token: '', foreground: '0D004D' },
    ],
    colors: {
      'editor.background': '#FFFFFF',
      'editor.foreground': '#0D004D',
      'editorLineNumber.foreground': '#5A5377',
      'editorLineNumber.activeForeground': '#0D004D',
      'editorGutter.background': '#F7F6FB',
      'editor.lineHighlightBackground': '#F7F6FB',
      'editor.selectionBackground': '#C1BFFF',
      'editorIndentGuide.background1': '#EDECF6',
      'editorWidget.background': '#FFFFFF',
      'editorWidget.border': '#EDECF6',
    },
  });
};

interface SchemaViewerProps {
  content: string;
  schemaType: string;
  /** The shared find query. Drives the line tint and token highlight. */
  query?: string;
  /** Index into `findCodeMatches(...)` of the match to centre. */
  activeMatch?: number;
  /** Called when the editor is asked for a find — so ⌘F focuses the page's field. */
  onRequestFind?: () => void;
  compact?: boolean;
  /** Rendered above the editor; the page puts its own toolbar buttons here. */
  isFullscreen?: boolean;
  onFullscreenChange?: (open: boolean) => void;
}

export function SchemaViewer({
  content,
  schemaType,
  query = '',
  activeMatch = 0,
  onRequestFind,
  compact = false,
  isFullscreen = false,
  onFullscreenChange,
}: SchemaViewerProps) {
  const monacoLanguage = getMonacoLanguage(schemaType ?? '', content);
  // The same function the page's match rail uses, so the text being searched
  // and the text being rendered cannot drift apart.
  const formatted = useMemo(() => displayedContent(schemaType ?? '', content), [schemaType, content]);

  const body = (
    <EditorBody
      content={formatted}
      monacoLanguage={monacoLanguage}
      query={query}
      activeMatch={activeMatch}
      onRequestFind={onRequestFind}
      height={compact || isFullscreen ? '100%' : '70vh'}
    />
  );

  return (
    // `h-full`, not `flex-1`. The caller sizes the pane (570px on the schema
    // page) on a plain block wrapper, and `flex-1` on a child of a non-flex
    // parent does nothing at all — the editor collapsed to zero height and the
    // code view rendered blank while its match rail beside it worked fine.
    <div className={cn('flex flex-col', compact && 'h-full overflow-hidden')}>
      <div
        className={cn('overflow-hidden', compact ? 'flex-1 flex flex-col min-h-0' : '')}
        style={{ borderRadius: 8 }}
      >
        {body}
      </div>

      {isFullscreen &&
        onFullscreenChange &&
        createPortal(
          <FullscreenModal label={schemaType ?? ''} onClose={() => onFullscreenChange(false)}>
            <EditorBody
              content={formatted}
              monacoLanguage={monacoLanguage}
              query={query}
              activeMatch={activeMatch}
              onRequestFind={onRequestFind}
              height="100%"
            />
          </FullscreenModal>,
          document.body,
        )}
    </div>
  );
}

interface EditorBodyProps {
  content: string;
  monacoLanguage: string;
  height: string;
  query: string;
  activeMatch: number;
  onRequestFind?: () => void;
}

function EditorBody({
  content,
  monacoLanguage,
  height,
  query,
  activeMatch,
  onRequestFind,
}: EditorBodyProps) {
  const editorRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);
  const decorationsRef = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null);
  // Kept current from an effect, not assigned during render. Writing to a
  // ref while rendering is a side effect in render — `react-hooks/refs` says
  // so — and this one only has to be right by the time Monaco's keybinding
  // fires, which is after mount.
  const findRef = useRef(onRequestFind);
  useEffect(() => {
    findRef.current = onRequestFind;
  }, [onRequestFind]);

  const matches = useMemo(() => findCodeMatches(content, query), [content, query]);

  const onMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current = editor;
    decorationsRef.current = editor.createDecorationsCollection([]);

    // Rebind ⌘F / Ctrl+F to the page's own field. Monaco's find widget is a
    // second search box over one of three views, bound to the key people
    // already press, so it has to give the key up rather than compete.
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyF, () => {
      findRef.current?.();
    });
    // F3 and ⌘G reach the same widget by other routes.
    editor.addCommand(monaco.KeyCode.F3, () => findRef.current?.());
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyG, () => findRef.current?.());
  }, []);

  // Line tint, gutter tint and token highlight, recomputed whenever the query
  // or the body changes.
  useEffect(() => {
    const collection = decorationsRef.current;
    if (!collection) return;

    // Two decorations per match, because Monaco draws them in different
    // layers and only one of them is safe to give a solid background.
    //
    // The row tint has to be `isWholeLine` + `className`, which lands in the
    // view-overlays layer BEHIND the text. `blockClassName` — which this used
    // first — paints a block in the content layer instead, so a solid fill on
    // it covered the line and the matched lines rendered blank.
    //
    // The token highlight has to be `inlineClassName`, which styles the text
    // span itself rather than drawing anything over it.
    collection.set(
      matches.flatMap((m) => [
        {
          range: {
            startLineNumber: m.line,
            startColumn: 1,
            endLineNumber: m.line,
            endColumn: 1,
          },
          options: {
            isWholeLine: true,
            className: 'schema-find-line',
            // Extends the tint across the gutter beside the line.
            marginClassName: 'schema-find-margin',
          },
        },
        {
          range: {
            startLineNumber: m.line,
            startColumn: m.column,
            endLineNumber: m.line,
            endColumn: m.column + query.length,
          },
          options: { inlineClassName: 'schema-find-token' },
        },
      ]),
    );
  }, [matches, query]);

  // Centre the active match. `revealLineInCenter` rather than `revealLine` so
  // a hit near the bottom of the body does not land on the last visible row
  // with no context under it.
  useEffect(() => {
    const editor = editorRef.current;
    const target = matches[activeMatch];
    if (!editor || !target) return;
    editor.revealLineInCenter(target.line);
    editor.setPosition({ lineNumber: target.line, column: target.column });
  }, [matches, activeMatch]);

  return (
    <Editor
      height={height}
      language={monacoLanguage}
      value={content}
      theme={PEARSON_MONACO_THEME}
      beforeMount={defineTheme}
      onMount={onMount}
      options={{
        readOnly: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        wordWrap: 'off',
        fontSize: 12,
        lineHeight: 20,
        lineNumbers: 'on',
        lineNumbersMinChars: 3,
        lineDecorationsWidth: 8,
        folding: true,
        renderLineHighlight: 'none',
        // Belt and braces alongside the rebound keys: with the widget's seeding
        // and in-selection behaviour off, nothing re-opens it on selection.
        find: {
          addExtraSpaceOnTop: false,
          seedSearchStringFromSelection: 'never',
          autoFindInSelection: 'never',
          loop: false,
        },
        fontFamily:
          'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
      }}
      loading={<p className="text-sm text-ink-mute p-4">Loading editor…</p>}
    />
  );
}

/**
 * Exported because the Fields view needs the same overlay: the page's one
 * Expand button serves every view, and it was honoured by this one alone.
 *
 * `label` rather than `schemaType` — the code view names the body's format
 * there, and the fields view names the view. The caller decides what the
 * header says because only the caller knows what it is showing.
 */
export function FullscreenModal({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Schema fullscreen view"
      className="fixed inset-0 z-50 flex flex-col bg-paper-card"
    >
      <div className="flex items-center gap-3 px-4 py-2 border-b border-paper-line bg-paper-raised shrink-0">
        <span className="font-mono text-xs text-ink-mute">{label}</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          className="flex items-center justify-center border border-light text-accent"
          style={{ width: 34, height: 34, borderRadius: 4 }}
          aria-label="Close fullscreen"
          title="Close fullscreen (Escape)"
        >
          <X size={16} />
        </button>
      </div>
      <div className="flex-1 overflow-hidden flex flex-col">{children}</div>
    </div>
  );
}
