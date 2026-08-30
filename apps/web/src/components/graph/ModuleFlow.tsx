"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { ModuleGraph } from "@/lib/graph";
import styles from "./ModuleFlow.module.css";

type ModuleData = {
  label: string;
  symbols: number;
  layer: number;
  dimmed: boolean;
  active: boolean;
};

// Warm categorical palette — no blue, no purple.
const LAYER_HUES = ["#f2a63c", "#ff7a52", "#e2557a", "#5fb87a", "#d98324", "#c2a83e"];

function ModuleNode({ data }: NodeProps<Node<ModuleData>>) {
  const hue = LAYER_HUES[data.layer % LAYER_HUES.length];
  const short = data.label.length > 24 ? `…${data.label.slice(-23)}` : data.label;
  return (
    <div
      className={styles.node}
      data-active={data.active}
      style={{
        opacity: data.dimmed ? 0.28 : 1,
        // @ts-expect-error custom prop
        "--hue": hue,
      }}
    >
      <Handle type="target" position={Position.Top} className={styles.handle} />
      <span className={styles.nodeDot} style={{ background: hue }} />
      <div className={styles.nodeText}>
        <span className={styles.nodeLabel} title={data.label}>
          {short}
        </span>
        <span className={styles.nodeSub}>{data.symbols} symbols</span>
      </div>
      <Handle type="source" position={Position.Bottom} className={styles.handle} />
    </div>
  );
}

const nodeTypes = { module: ModuleNode };

export function ModuleFlow({
  graph,
  compact,
  onSelect,
}: {
  graph: ModuleGraph;
  compact?: boolean;
  onSelect?: (id: string | null) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);

  // Reset selection when the underlying graph changes.
  useEffect(() => setSelected(null), [graph]);

  const neighbors = useMemo(() => {
    if (!selected) return null;
    const set = new Set<string>([selected]);
    for (const e of graph.edges) {
      if (e.src === selected) set.add(e.dst);
      if (e.dst === selected) set.add(e.src);
    }
    return set;
  }, [selected, graph.edges]);

  const nodes: Node<ModuleData>[] = useMemo(
    () =>
      graph.nodes.map((n) => ({
        id: n.id,
        type: "module",
        position: { x: n.x, y: n.y },
        data: {
          label: n.label,
          symbols: n.symbol_count,
          layer: n.layer,
          active: selected === n.id,
          dimmed: neighbors ? !neighbors.has(n.id) : false,
        },
      })),
    [graph.nodes, selected, neighbors],
  );

  const edges: Edge[] = useMemo(
    () =>
      graph.edges.map((e) => {
        const strong = e.confidence >= 0.7;
        const touches = !neighbors || (neighbors.has(e.src) && neighbors.has(e.dst));
        return {
          id: `${e.src}->${e.dst}`,
          source: e.src,
          target: e.dst,
          animated: strong && touches,
          style: {
            stroke: strong ? "var(--accent)" : "var(--faint)",
            strokeWidth: Math.min(1 + e.weight / 3, 3.5),
            strokeDasharray: strong ? undefined : "5 4",
            opacity: touches ? (strong ? 0.75 : 0.5) : 0.08,
          },
        };
      }),
    [graph.edges, neighbors],
  );

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      setSelected((cur) => {
        const next = cur === node.id ? null : node.id;
        onSelect?.(next);
        return next;
      });
    },
    [onSelect],
  );

  return (
    <div className={styles.canvas} data-compact={compact ? "true" : undefined}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={() => {
          setSelected(null);
          onSelect?.(null);
        }}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.15}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        nodesConnectable={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.5} color="var(--line-strong)" />
        <Controls className={styles.controls} showInteractive={false} />
        <MiniMap
          className={styles.minimap}
          pannable
          zoomable
          maskColor="var(--flow-mask)"
          nodeColor={(n) => LAYER_HUES[((n.data as ModuleData)?.layer ?? 0) % LAYER_HUES.length]}
        />
      </ReactFlow>
      {selected ? (
        <div className={styles.hintBadge}>
          Highlighting <strong>{graph.nodes.find((n) => n.id === selected)?.label}</strong> &amp;
          its neighbors · click empty space to reset
        </div>
      ) : (
        <div className={styles.hintBadge}>Click a module to isolate its dependencies · drag to reposition · scroll to zoom</div>
      )}
    </div>
  );
}
