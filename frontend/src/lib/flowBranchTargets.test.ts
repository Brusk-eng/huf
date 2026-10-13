import { describe, it, expect } from 'vitest';
import { buildBranchOptionLabels } from './flowBranchTargets';

describe('buildBranchOptionLabels', () => {
  it('keeps unique labels unchanged', () => {
    const labels = buildBranchOptionLabels([
      { id: 'n1', data: { label: 'Send email' } },
      { id: 'n2', data: { label: 'Wait' } },
    ]);
    expect(labels.get('n1')).toBe('Send email');
    expect(labels.get('n2')).toBe('Wait');
  });

  it('appends a short id suffix only to duplicated labels', () => {
    const labels = buildBranchOptionLabels([
      { id: 'abcdef123456', data: { label: 'Transform Data' } },
      { id: 'zz99', data: { label: 'Transform Data' } },
      { id: 'solo', data: { label: 'Loop' } },
    ]);
    expect(labels.get('abcdef123456')).toBe('Transform Data · abcdef12…');
    expect(labels.get('zz99')).toBe('Transform Data · zz99');
    expect(labels.get('solo')).toBe('Loop');
  });

  it('falls back to the node id when there is no label', () => {
    const labels = buildBranchOptionLabels([{ id: 'node-1', data: {} }]);
    expect(labels.get('node-1')).toBe('node-1');
  });
});
