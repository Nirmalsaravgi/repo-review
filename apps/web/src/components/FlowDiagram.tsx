"use client";

import type { FlowStep } from "@/lib/understanding";
import styles from "./FlowDiagram.module.css";

export function FlowDiagram({
  title,
  steps,
}: {
  title?: string;
  steps: FlowStep[];
}) {
  const nodes = linearize(title, steps);
  if (nodes.length === 0) {
    return <p className={styles.empty}>No call path indexed for this flow.</p>;
  }
  return (
    <ol className={styles.lane}>
      {nodes.map((n, i) => (
        <li key={`${n.name}-${i}`} className={styles.item}>
          <div className={styles.rail}>
            <span className={styles.dot} data-kind={n.kind} />
            {i < nodes.length - 1 ? <span className={styles.line} /> : null}
          </div>
          <div className={styles.card} data-approx={n.confidence < 0.7}>
            <code className={styles.name}>{n.name}</code>
            {n.kind && n.kind !== "calls" ? <span className={styles.kind}>{n.kind}</span> : null}
            {n.confidence < 0.7 ? (
              <span className={styles.approx}>likely · {Math.round(n.confidence * 100)}%</span>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

function linearize(title: string | undefined, steps: FlowStep[]) {
  if (steps.length === 0) {
    return title ? [{ name: title, kind: "entry", confidence: 1 }] : [];
  }
  const out: { name: string; kind: string; confidence: number }[] = [];
  out.push({ name: steps[0].src, kind: "entry", confidence: 1 });
  for (const s of steps) {
    if (out[out.length - 1]?.name !== s.dst) {
      out.push({ name: s.dst, kind: s.kind, confidence: s.confidence });
    }
  }
  return out.slice(0, 16);
}
