// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TimeStamp, relativeTime } from './TimeStamp';

const NOW = new Date('2026-10-14T12:00:00');

describe('TimeStamp', () => {
  it('is relative with the full time in the title and no raw microseconds', () => {
    expect(relativeTime('2026-10-14 11:57:00.123456', NOW)).toBe('3 min ago');
    expect(relativeTime('2026-09-01 11:57:00', NOW)).not.toMatch(/ago|\d{4}-\d{2}/);
    const d = new Date(Date.now() - 3 * 60_000);
    const p = (n: number) => String(n).padStart(2, '0');
    const raw = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.123456`;
    render(<TimeStamp value={raw} />);
    const el = screen.getByText(/ago|just now/);
    expect(el.getAttribute('title')).toMatch(/2026/);
    expect(el.textContent).not.toMatch(/\.\d{6}/);
  });

  it('shows a dash for missing values', () => {
    render(<TimeStamp value={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
