import { describe, it, expect } from 'vitest';
import { actionHasConfiguredContent } from './flowNodeConfigured';
import type { ActionConfig } from '../types/flow.types';

const check = (actionConfig: ActionConfig | undefined, configured = true) =>
  actionHasConfiguredContent({ actionConfig, configured });

describe('actionHasConfiguredContent', () => {
  it('is false for a freshly picked action type with no fields filled', () => {
    // NodeSelectionModal sets configured: true as soon as the type is picked.
    expect(check({ type: 'agent-run' }, true)).toBe(false);
    expect(check({ type: 'http-request', url: '   ' }, true)).toBe(false);
  });

  it('is true once a meaningful field has content', () => {
    expect(check({ type: 'agent-run', agent_name: 'support' })).toBe(true);
    expect(check({ type: 'tool-call', tool_name: 'search' })).toBe(true);
    expect(check({ type: 'http-request', url: 'https://example.com' })).toBe(true);
    expect(check({ type: 'loop', iterate_over: 'items' })).toBe(true);
  });

  it('treats any condition branch target as configuration', () => {
    expect(check({ type: 'condition', true_node: 'n1' })).toBe(true);
    expect(check({ type: 'condition' })).toBe(false);
  });

  it('treats approval assignees as configuration', () => {
    expect(check({ type: 'human.approval', assigned_user: ['a@b.c'] })).toBe(true);
    expect(check({ type: 'human.approval', assigned_user: [] })).toBe(false);
  });

  it('treats transform mappings as configuration', () => {
    expect(
      check({ type: 'transform', transformations: [{ source_field: 'a', target_field: 'b' }] }),
    ).toBe(true);
    expect(check({ type: 'transform', transformations: [] })).toBe(false);
  });

  it('is false when there is no action config at all', () => {
    expect(check(undefined, true)).toBe(false);
  });
});
