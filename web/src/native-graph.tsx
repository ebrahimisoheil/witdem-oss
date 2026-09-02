import type { CSSProperties, MouseEvent, ReactNode } from "react";

export type NativeGraphNode<T> = {
  id: string;
  position: { x: number; y: number };
  data: T;
  style?: CSSProperties;
  selected?: boolean;
};

export type NativeGraphEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
  animated?: boolean;
  style?: CSSProperties;
  markerEnd?: unknown;
  labelStyle?: CSSProperties;
  type?: string;
};

const numeric = (value: CSSProperties["width"] | CSSProperties["height"], fallback: number) =>
  typeof value === "number" ? value : Number.parseFloat(String(value || "")) || fallback;

export function NativeGraph<T>({
  graph,
  renderNode,
  onNodeClick,
  onPaneClick,
  className = "",
  nodeWidth = 184,
  nodeHeight = 96,
}: {
  graph: { nodes: Array<NativeGraphNode<T>>; edges: NativeGraphEdge[] };
  renderNode: (node: NativeGraphNode<T>) => ReactNode;
  onNodeClick?: (event: MouseEvent, node: NativeGraphNode<T>) => void;
  onPaneClick?: () => void;
  className?: string;
  nodeWidth?: number;
  nodeHeight?: number;
}) {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const dimensions = new Map(graph.nodes.map((node) => [node.id, {
    width: numeric(node.style?.width, nodeWidth),
    height: numeric(node.style?.height, nodeHeight),
  }]));
  const width = Math.max(720, ...graph.nodes.map((node) => node.position.x + (dimensions.get(node.id)?.width || nodeWidth) + 72));
  const height = Math.max(360, ...graph.nodes.map((node) => node.position.y + (dimensions.get(node.id)?.height || nodeHeight) + 72));
  const markerId = `native-arrow-${graph.nodes.length}-${graph.edges.length}`;

  return <div className={`relative h-full overflow-auto bg-[#fbfbf8] ${className}`} onClick={(event) => { if (event.target === event.currentTarget) onPaneClick?.(); }}>
    <div className="relative min-h-full min-w-full bg-[radial-gradient(#d9dce2_0.8px,transparent_0.8px)] [background-size:20px_20px]" style={{ width, height }} onClick={(event) => { if (event.target === event.currentTarget) onPaneClick?.(); }}>
      <svg className="pointer-events-none absolute inset-0 z-0" width={width} height={height} aria-hidden="true">
        <defs>
          <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#8e87a0" />
          </marker>
        </defs>
        {graph.edges.map((edge) => {
          const source = byId.get(edge.source);
          const target = byId.get(edge.target);
          if (!source || !target) return null;
          const sourceSize = dimensions.get(source.id)!;
          const targetSize = dimensions.get(target.id)!;
          const sx = source.position.x + sourceSize.width;
          const sy = source.position.y + sourceSize.height / 2;
          const tx = target.position.x;
          const ty = target.position.y + targetSize.height / 2;
          const distance = Math.max(42, Math.abs(tx - sx) * 0.42);
          const path = tx >= sx
            ? `M ${sx} ${sy} C ${sx + distance} ${sy}, ${tx - distance} ${ty}, ${tx} ${ty}`
            : `M ${sx} ${sy} C ${sx + 48} ${sy}, ${sx + 48} ${ty + 44}, ${(sx + tx) / 2} ${ty + 44} S ${tx - 44} ${ty}, ${tx} ${ty}`;
          const stroke = String(edge.style?.stroke || "#98a0ad");
          return <path key={edge.id} d={path} fill="none" stroke={stroke} strokeWidth={Number(edge.style?.strokeWidth || 1.6)} strokeDasharray={String(edge.style?.strokeDasharray || "")} markerEnd={`url(#${markerId})`} className={edge.animated ? "animate-pulse" : ""} />;
        })}
      </svg>
      {graph.edges.map((edge) => {
        if (!edge.label) return null;
        const source = byId.get(edge.source);
        const target = byId.get(edge.target);
        if (!source || !target) return null;
        const sourceSize = dimensions.get(source.id)!;
        const targetSize = dimensions.get(target.id)!;
        const x = (source.position.x + sourceSize.width + target.position.x) / 2;
        const y = (source.position.y + sourceSize.height / 2 + target.position.y + targetSize.height / 2) / 2;
        return <span key={`label-${edge.id}`} className="pointer-events-none absolute z-[1] -translate-x-1/2 -translate-y-1/2 rounded-full border border-[#ddd8e3] bg-white px-2 py-1 text-[9px] font-semibold text-[#6b6470] shadow-sm" style={{ left: x, top: y }}>{edge.label}</span>;
      })}
      {graph.nodes.map((node) => <button key={node.id} type="button" className="absolute z-[2] block border-0 bg-transparent p-0 text-left" style={{ left: node.position.x, top: node.position.y, width: dimensions.get(node.id)?.width, height: dimensions.get(node.id)?.height }} onClick={(event) => { event.stopPropagation(); onNodeClick?.(event, node); }}>
        {renderNode(node)}
      </button>)}
    </div>
  </div>;
}
