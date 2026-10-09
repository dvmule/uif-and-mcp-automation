import '@xyflow/react/dist/style.css';
import { useMemo, useCallback, useState } from 'react';
import dagre from '@dagrejs/dagre';
import {
  ReactFlow,
  Handle,
  Panel,
  Position,
  useReactFlow,
  type Node,
  type Edge,
  type NodeTypes,
} from '@xyflow/react';
import { toPng } from 'html-to-image';
import { Download, ZoomIn, ZoomOut, Maximize, Copy, Check, TriangleAlert } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/utils';
import { buildSchemaGraph, splitHit, graphNodeMatches } from '../schema-body';
import { graphImageName, triggerDownload } from '../utils';
import type { ParsedSchemaBody, GraphRow } from '../schema-body';

/**
 * 5a — the schema body as a node graph, built on the app's existing React Flow
 * + dagre setup rather than a second graph library.
 *
 * Every node is an object or array in the body, its rows are that object's
 * scalars, and every edge leaves the row it belongs to rather than the node's
 * centre — an edge from the middle of a six-row node tells you nothing about
 * which key it came from. For the customer schema that is seven nodes: the
 * record root, `fields[]`, its three field objects, `metadata`, `owner` and
 * `piiFields`.
 *
 * A search match rings its node and turns amber every edge on the path back to
 * the root, so a hit deep in the tree is traceable rather than just visible.
 */

const NODE_W = 210;
const ROW_H = 18;
/** 10px padding top and bottom, plus the title row. */
const NODE_CHROME_H = 20 + 18;

// The design's three syntax colours, the same set the code view's Monaco theme
// uses — a key is Amethyst in both readings of the body.
const TOK_KEY = '#512EAB';
const TOK_VALUE = '#7B1571';
const TOK_STRUCTURE = '#0D004D';

interface GraphNodeData extends Record<string, unknown> {
  title: string;
  rows: GraphRow[];
  query: string;
  /** This node's own title or one of its rows contains the query. */
  matched: boolean;
  /** Row indices this node has outgoing edges from, so they can be anchored. */
  handleRows: number[];
  piiRow: boolean;
}

type SchemaFlowNode = Node<GraphNodeData>;

function Highlighted({ text, query }: { text: string; query: string }) {
  const { pre, match, post, hit } = splitHit(text, query);
  if (!hit) return <>{text}</>;
  return (
    <>
      {pre}
      <mark className="bg-amber-tint text-ink" style={{ borderRadius: 2 }}>
        {match}
      </mark>
      {post}
    </>
  );
}

function SchemaNode({ data }: { data: GraphNodeData }) {
  return (
    <div
      className="bg-paper-card"
      style={{
        width: NODE_W,
        padding: '10px 12px',
        borderRadius: 8,
        border: data.matched ? '2px solid #FFCE00' : '1px solid #C1BFFF',
        boxShadow: data.matched
          ? '0 2px 6px rgba(13,0,77,.07)'
          : '0 1px 2px rgba(13,0,77,.06)',
      }}
    >
      {/* One target handle, on the left, doubling as the design's end dot. */}
      <Handle
        type="target"
        position={Position.Left}
        style={{
          width: 3.5,
          height: 3.5,
          minWidth: 3.5,
          minHeight: 3.5,
          border: 'none',
          // The dark amber of the FIND family (#FFCE00/#FFE67F/#FFF9E0), not
          // status warn — a search hit is not a warning. It happens to be the
          // hex that `signal-warn` used to hold, so it is spelled out here
          // rather than left looking like a token that moved to Echo's #BB4D00.
          background: data.matched ? '#7F6701' : '#512EAB',
        }}
      />

      <div className="flex items-center gap-1.5" style={{ height: 18 }}>
        <span className="font-mono text-[11px] font-semibold leading-[18px] truncate" style={{ color: TOK_STRUCTURE }}>
          <Highlighted text={data.title} query={data.query} />
        </span>
        {data.piiRow && <Badge tone="warn">PII</Badge>}
      </div>

      {data.rows.map((row, i) => (
        <div
          key={row.key}
          className="relative flex items-baseline gap-1.5 font-mono text-[11px] leading-[18px]"
          style={{ height: ROW_H }}
        >
          <span className="shrink-0 truncate" style={{ color: TOK_KEY }}>
            <Highlighted text={row.key} query={data.query} />
          </span>
          <span className="truncate" style={{ color: TOK_VALUE }}>
            <Highlighted text={row.value} query={data.query} />
          </span>

          {/* A source handle per row that actually has an outgoing edge, so the
            * edge leaves the key it belongs to. Rows without a branch get no
            * handle at all rather than an unused dot. */}
          {data.handleRows.includes(i) && (
            <Handle
              id={`row-${i}`}
              type="source"
              position={Position.Right}
              style={{
                top: ROW_H / 2,
                width: 3.5,
                height: 3.5,
                minWidth: 3.5,
                minHeight: 3.5,
                border: 'none',
                background: '#512EAB',
              }}
            />
          )}
        </div>
      ))}
    </div>
  );
}

const nodeTypes: NodeTypes = { schema: SchemaNode as NodeTypes[string] };

function nodeHeight(rowCount: number): number {
  return NODE_CHROME_H + rowCount * ROW_H;
}

function applyLayout(nodes: SchemaFlowNode[], edges: Edge[]): SchemaFlowNode[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', ranksep: 110, nodesep: 28 });

  nodes.forEach((n) => {
    g.setNode(n.id, { width: NODE_W, height: nodeHeight(n.data.rows.length) });
  });
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    const h = nodeHeight(n.data.rows.length);
    return { ...n, position: { x: pos.x - NODE_W / 2, y: pos.y - h / 2 } };
  });
}

interface SchemaGraphViewProps {
  body: ParsedSchemaBody;
  /** Raw body, for the copy and download actions. */
  content: string;
  subject: string;
  /** Apicurio artifact type, so the download gets the right extension. */
  /** The version on screen, which the downloaded file is named for. */
  version: string;
  query: string;
}

export function SchemaGraphView({
  body,
  content,
  subject,
  version,
  query,
}: SchemaGraphViewProps) {
  const piiFields = useMemo(
    () =>
      new Set(
        body.fields.filter((f) => f.pii).map((f) => f.name),
      ),
    [body.fields],
  );

  const { nodes, edges, matchedCount } = useMemo(() => {
    const spec = buildSchemaGraph(body.value);
    if (spec.nodes.length === 0) {
      return { nodes: [] as SchemaFlowNode[], edges: [] as Edge[], matchedCount: 0 };
    }

    // Same predicate the page's match count uses, so the ring and the figure
    // can never disagree.
    const matched = new Set(
      spec.nodes.filter((n) => graphNodeMatches(n, query)).map((n) => n.id),
    );

    // An edge goes amber when the subtree it leads into holds a match, so the
    // trail from the root to a deep hit is continuous. Computed by walking up
    // from each matched node, which is cheaper than a subtree scan per edge
    // and cannot miss a node the scan would not have reached.
    const parentOf = new Map<string, string>();
    for (const e of spec.edges) parentOf.set(e.target, e.source);
    const onMatchPath = new Set<string>();
    for (const id of matched) {
      let child = id;
      let parent = parentOf.get(child);
      while (parent) {
        onMatchPath.add(`${parent}->${child}`);
        child = parent;
        parent = parentOf.get(child);
      }
    }

    const handleRowsByNode = new Map<string, number[]>();
    for (const e of spec.edges) {
      const list = handleRowsByNode.get(e.source) ?? [];
      list.push(e.sourceRow);
      handleRowsByNode.set(e.source, list);
    }

    const flowNodes: SchemaFlowNode[] = spec.nodes.map((n) => ({
      id: n.id,
      type: 'schema',
      position: { x: 0, y: 0 },
      data: {
        title: n.title,
        rows: n.rows,
        query,
        matched: matched.has(n.id),
        handleRows: handleRowsByNode.get(n.id) ?? [],
        piiRow: piiFields.has(n.title),
      },
    }));

    const flowEdges: Edge[] = spec.edges.map((e) => {
      const hot = onMatchPath.has(e.id);
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceRow >= 0 ? `row-${e.sourceRow}` : undefined,
        // React Flow's default edge is the cubic bézier the design asks for.
        type: 'default',
        style: {
          stroke: hot ? '#FFCE00' : '#C1BFFF',
          strokeWidth: hot ? 2 : 1.5,
        },
      };
    });

    return {
      nodes: applyLayout(flowNodes, flowEdges),
      edges: flowEdges,
      matchedCount: matched.size,
    };
  }, [body.value, query, piiFields]);

  if (nodes.length === 0) {
    return (
      <div
        className="flex items-center justify-center bg-paper-raised"
        style={{ height: 570, borderRadius: 8 }}
      >
        <p className="text-sm text-ink-mute">
          {body.parsed
            ? 'This body has no nested structure to draw. Read it in Code view.'
            : 'This body is not JSON, so it cannot be drawn as a graph. Read it in Code view.'}
        </p>
      </div>
    );
  }

  return (
    <div
      className="relative overflow-hidden bg-paper-raised"
      style={{ height: 570, borderRadius: 8 }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        panOnDrag
        zoomOnScroll
        proOptions={{ hideAttribution: true }}
      >
        <Panel position="top-right">
          <GraphToolbar
            content={content}
            subject={subject}
            version={version}
            query={query}
            matchedCount={matchedCount}
          />
        </Panel>
        <Panel position="bottom-left">
          <Legend query={query} />
        </Panel>
      </ReactFlow>
    </div>
  );
}

function GraphToolbar({
  content,
  subject,
  version,
  query,
  matchedCount,
}: {
  content: string;
  subject: string;
  version: string;
  query: string;
  matchedCount: number;
}) {
  const { zoomIn, zoomOut, fitView, getNodes } = useReactFlow();
  const [copied, setCopied] = useState(false);
  const [pngState, setPngState] = useState<'idle' | 'busy' | 'failed'>('idle');

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [content]);

  /**
   * Saves a picture of the graph, not the schema body.
   *
   * This button used to download the body — which the page toolbar above
   * already does, correctly named by schema type. Two buttons for the same
   * file, and neither of them the thing this pane actually shows.
   *
   * The whole graph is exported, not the visible viewport: the node bounds set
   * the image size and the transform is overridden so panning and zooming on
   * screen cannot crop what lands in the file. The ground is the pane's own
   * #F7F6FB rather than white, so the PNG matches what it was taken from, and
   * `pixelRatio: 2` keeps 11px labels legible.
   */
  const handleDownloadPng = useCallback(async () => {
    const el = document.querySelector<HTMLElement>('.react-flow__viewport');
    const nodes = getNodes();
    if (!el || nodes.length === 0) return;

    setPngState('busy');
    try {
      // Bounds are computed from positions plus the RENDERED size of each
      // node, not from `getNodesBounds`. These nodes carry no explicit width —
      // they are sized by their content through CSS — so that helper returns
      // the span of the positions alone and treats every node as zero by zero.
      // For this graph it reported 640px across where the nodes actually reach
      // 850, and the right-hand column was cropped out of every export.
      const size = new Map<string, { w: number; h: number }>();
      el.querySelectorAll<HTMLElement>('.react-flow__node').forEach((n) => {
        const id = n.getAttribute('data-id');
        if (id) size.set(id, { w: n.offsetWidth, h: n.offsetHeight });
      });

      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const n of nodes) {
        const s = size.get(n.id) ?? {
          w: n.measured?.width ?? 0,
          h: n.measured?.height ?? 0,
        };
        minX = Math.min(minX, n.position.x);
        minY = Math.min(minY, n.position.y);
        maxX = Math.max(maxX, n.position.x + s.w);
        maxY = Math.max(maxY, n.position.y + s.h);
      }
      // Thrown rather than returned: this sits after `setPngState('busy')`
      // inside the try, so a bare return left the button spinning for good
      // with no way back to idle. The catch below owns every failure.
      if (!Number.isFinite(minX)) throw new Error('Graph bounds could not be measured');

      // Edges bow outside the node box, so the padding is not only margin.
      const PAD = 32;
      const width = Math.ceil(maxX - minX) + PAD * 2;
      const height = Math.ceil(maxY - minY) + PAD * 2;

      const dataUrl = await toPng(el, {
        backgroundColor: '#F7F6FB',
        width,
        height,
        pixelRatio: 2,
        style: {
          width: `${width}px`,
          height: `${height}px`,
          transform: `translate(${-minX + PAD}px, ${-minY + PAD}px) scale(1)`,
          transformOrigin: 'top left',
        },
      });
      triggerDownload(dataUrl, graphImageName(subject, version));
      setPngState('idle');
    } catch {
      // A silent no-op would be indistinguishable from a browser that blocked
      // the save, so the button says it failed and stays clickable.
      setPngState('failed');
      setTimeout(() => setPngState('idle'), 4000);
    }
  }, [getNodes, subject, version]);

  const cellClass = 'flex items-center justify-center shrink-0 text-accent';
  const cellStyle = { width: 34, height: 34, borderRadius: 999 } as const;

  return (
    <div className="flex flex-col items-end gap-2">
      {query && (
        <div
          className="flex items-center gap-2 bg-paper-card"
          style={{
            width: 280,
            height: 40,
            borderRadius: 999,
            border: '1px solid #512EAB',
            boxShadow: '0 8px 24px rgba(13,0,77,.10)',
            padding: '0 14px',
          }}
        >
          <span className="flex-1 min-w-0 truncate font-mono text-[13px] text-ink">{query}</span>
          <span className="shrink-0 text-xs text-ink-mute">
            {matchedCount} node{matchedCount === 1 ? '' : 's'}
          </span>
        </div>
      )}

      <div
        className="flex items-center gap-1 bg-paper-card"
        style={{
          borderRadius: 999,
          border: '1px solid #E2E0F0',
          boxShadow: '0 8px 24px rgba(13,0,77,.10)',
          padding: 5,
        }}
      >
        <button
          type="button"
          onClick={handleDownloadPng}
          disabled={pngState === 'busy'}
          className={cn(cellClass, pngState === 'failed' && 'text-signal-fail')}
          style={cellStyle}
          aria-label={
            pngState === 'failed'
              ? 'Download the graph as a PNG — the last attempt failed'
              : 'Download the graph as a PNG'
          }
          title={
            pngState === 'failed'
              ? "Couldn't render the graph to an image"
              : `Download ${graphImageName(subject, version)}`
          }
        >
          {pngState === 'failed' ? <TriangleAlert size={16} /> : <Download size={16} />}
        </button>
        <button type="button" onClick={() => zoomOut({ duration: 200 })} className={cellClass} style={cellStyle} aria-label="Zoom out">
          <ZoomOut size={16} />
        </button>
        <button type="button" onClick={() => zoomIn({ duration: 200 })} className={cellClass} style={cellStyle} aria-label="Zoom in">
          <ZoomIn size={16} />
        </button>
        <button
          type="button"
          onClick={() => fitView({ duration: 300 })}
          className={cn(cellClass, 'bg-paper')}
          style={cellStyle}
          aria-label="Fit the graph to the view"
          title="Fit to view"
        >
          <Maximize size={16} />
        </button>
        <button type="button" onClick={handleCopy} className={cellClass} style={cellStyle} aria-label="Copy the schema body">
          {copied ? <Check size={16} className="text-signal-ok-bold" /> : <Copy size={16} />}
        </button>
      </div>
    </div>
  );
}

function Legend({ query }: { query: string }) {
  return (
    <div
      className="flex items-center gap-3 bg-paper-card"
      style={{
        borderRadius: 999,
        border: '1px solid #E2E0F0',
        boxShadow: '0 1px 2px rgba(13,0,77,.06)',
        padding: '6px 14px',
      }}
    >
      {query && (
        <span className="flex items-center gap-1.5 text-xs text-ink-mute">
          <span aria-hidden style={{ width: 16, height: 2, background: '#FFCE00' }} />
          matches “{query}”
        </span>
      )}
      <span className="flex items-center gap-1.5 text-xs text-ink-mute">
        <Badge tone="warn">PII</Badge>
        listed in the metadata block
      </span>
    </div>
  );
}
