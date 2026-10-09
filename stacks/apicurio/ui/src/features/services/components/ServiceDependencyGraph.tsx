import '@xyflow/react/dist/style.css';
import { useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import dagre from '@dagrejs/dagre';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Panel,
  Position,
  useReactFlow,
  type Node,
  type Edge,
  type NodeTypes,
  type NodeMouseHandler,
} from '@xyflow/react';
import { Badge } from '@/components/ui/Badge';
import type { ServicePortfolioRead } from '../types/service.types';

const NODE_W = 176;
const NODE_H = 68;
const FOCAL_W = 200;
const FOCAL_H = 88;

interface NodeData extends Record<string, unknown> {
  name: string;
  domain: string;
  urn: string;
  isFocal: boolean;
  producesCount?: number;
  consumesCount?: number;
}

type ServiceNode = Node<NodeData>;

function FocalNode({ data }: { data: NodeData }) {
  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-accent" />
      <div className="bg-white border-2 border-accent rounded-lg px-3 py-2.5 shadow-sm w-[192px]">
        <p className="text-sm font-semibold text-ink leading-snug truncate">{data.name}</p>
        <div className="mt-1">
          <Badge tone="accent">{data.domain}</Badge>
        </div>
        <p className="text-xs text-ink-mute mt-1.5 font-mono">
          {data.producesCount} produces · {data.consumesCount} consumes
        </p>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-accent" />
    </>
  );
}

function NeighbourNode({ data }: { data: NodeData }) {
  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-paper-line" />
      <div className="bg-white border border-paper-line rounded-lg px-3 py-2 shadow-sm cursor-pointer hover:border-accent transition-colors w-[168px]">
        <p className="text-sm font-medium text-ink leading-snug truncate">{data.name}</p>
        <div className="mt-1">
          <Badge tone="accent">{data.domain}</Badge>
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-paper-line" />
    </>
  );
}

const nodeTypes: NodeTypes = {
  focal: FocalNode as NodeTypes[string],
  neighbour: NeighbourNode as NodeTypes[string],
};

function ResetButton() {
  const { fitView } = useReactFlow();
  return (
    <button
      type="button"
      onClick={() => fitView({ duration: 300 })}
      className="px-2.5 py-1 text-xs font-medium bg-white border border-paper-line rounded shadow-sm text-ink hover:bg-paper hover:border-accent transition-colors"
    >
      Reset view
    </button>
  );
}

function applyLayout(nodes: ServiceNode[], edges: Edge[]): ServiceNode[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', ranksep: 120, nodesep: 60 });

  nodes.forEach((n) => {
    const w = n.data.isFocal ? FOCAL_W : NODE_W;
    const h = n.data.isFocal ? FOCAL_H : NODE_H;
    g.setNode(n.id, { width: w, height: h });
  });
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    const w = n.data.isFocal ? FOCAL_W : NODE_W;
    const h = n.data.isFocal ? FOCAL_H : NODE_H;
    return { ...n, position: { x: pos.x - w / 2, y: pos.y - h / 2 } };
  });
}

interface ServiceDependencyGraphProps {
  data: ServicePortfolioRead;
}

export function ServiceDependencyGraph({ data }: ServiceDependencyGraphProps) {
  const navigate = useNavigate();

  const hasNoDeps =
    data.dependencies.upstream.length === 0 && data.dependencies.downstream.length === 0;

  const { nodes, edges } = useMemo(() => {
    const upstreamIds = new Set(data.dependencies.upstream.map((d) => d.ci_id));

    const rawNodes: ServiceNode[] = [
      {
        id: data.ci_id,
        type: 'focal',
        position: { x: 0, y: 0 },
        data: {
          name: data.name,
          domain: data.domain,
          urn: data.urn,
          isFocal: true,
          producesCount: data.produces.length,
          consumesCount: data.consumes.length,
        },
      },
      ...data.dependencies.upstream.map((dep) => ({
        id: dep.ci_id,
        type: 'neighbour',
        position: { x: 0, y: 0 } as { x: number; y: number },
        data: { name: dep.name, domain: dep.domain, urn: dep.urn, isFocal: false },
      })),
      // Downstream services the upstream list has not already placed. A
      // service can legitimately be both — it consumes a schema this one
      // produces AND produces one this one consumes — and React Flow requires
      // unique node ids: two nodes sharing a ci_id are silently collapsed to
      // one by `adoptUserNodes`, and dagre lays out whichever survived.
      //
      // The EDGES deliberately stay as they are: they carry direction in
      // their ids, so such a pair correctly draws one edge each way between
      // the same two nodes.
      ...data.dependencies.downstream
        .filter((dep) => !upstreamIds.has(dep.ci_id))
        .map((dep) => ({
          id: dep.ci_id,
          type: 'neighbour',
          position: { x: 0, y: 0 } as { x: number; y: number },
          data: { name: dep.name, domain: dep.domain, urn: dep.urn, isFocal: false },
        })),
    ];

    const rawEdges: Edge[] = [
      ...data.dependencies.upstream.map((dep) => ({
        id: `${dep.ci_id}->${data.ci_id}`,
        source: dep.ci_id,
        target: data.ci_id,
        label: `${dep.shared_schema_count} schema${dep.shared_schema_count !== 1 ? 's' : ''}`,
        labelStyle: { fontSize: 11, fill: '#5A5377' },
        labelBgStyle: { fill: '#F7F6FB', fillOpacity: 0.85 },
        labelBgPadding: [4, 2] as [number, number],
      })),
      ...data.dependencies.downstream.map((dep) => ({
        id: `${data.ci_id}->${dep.ci_id}`,
        source: data.ci_id,
        target: dep.ci_id,
        label: `${dep.shared_schema_count} schema${dep.shared_schema_count !== 1 ? 's' : ''}`,
        labelStyle: { fontSize: 11, fill: '#5A5377' },
        labelBgStyle: { fill: '#F7F6FB', fillOpacity: 0.85 },
        labelBgPadding: [4, 2] as [number, number],
      })),
    ];

    return { nodes: applyLayout(rawNodes, rawEdges), edges: rawEdges };
  }, [data]);

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      if (!node.data.isFocal) {
        navigate(`/services/${encodeURIComponent(node.data.urn as string)}`);
      }
    },
    [navigate],
  );

  if (hasNoDeps) {
    return (
      <div className="h-[500px] flex items-center justify-center">
        <p className="text-sm text-ink-mute italic">No service dependencies to display.</p>
      </div>
    );
  }

  return (
    <div className="h-[500px] rounded-lg overflow-hidden border border-paper-line">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll
        zoomOnScroll
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} color="#E2E0F0" />
        <Controls showInteractive={false} />
        <Panel position="top-right">
          <ResetButton />
        </Panel>
      </ReactFlow>
    </div>
  );
}
