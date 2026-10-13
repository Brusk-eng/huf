// Display helpers for node pickers used as branch/loop targets (condition
// True/False, loop body/done). Kept pure so the picker logic can be unit-tested.

export interface BranchTargetNode {
  id: string;
  data: { label?: string };
}

/**
 * Option label for each candidate node. Nodes that share a display label (e.g.
 * two "Transform Data" nodes) get a short id suffix so they can be told apart;
 * unique labels are returned unchanged.
 */
export function buildBranchOptionLabels(nodes: BranchTargetNode[]): Map<string, string> {
  const labelOf = (n: BranchTargetNode) => n.data.label || n.id;
  const counts = new Map<string, number>();
  for (const n of nodes) {
    counts.set(labelOf(n), (counts.get(labelOf(n)) ?? 0) + 1);
  }

  const result = new Map<string, string>();
  for (const n of nodes) {
    const label = labelOf(n);
    const shortId = n.id.length > 8 ? `${n.id.slice(0, 8)}…` : n.id;
    result.set(n.id, (counts.get(label) ?? 0) > 1 ? `${label} · ${shortId}` : label);
  }
  return result;
}
