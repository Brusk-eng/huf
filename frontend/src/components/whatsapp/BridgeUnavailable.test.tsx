// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LoadError } from './BridgeUnavailable';

describe('LoadError', () => {
  it('shows plain messages as they are', () => {
    render(<LoadError message="Gateway not found." />);
    expect(screen.getByText('Gateway not found.')).toBeInTheDocument();
    expect(screen.queryByText('Technical details')).toBeNull();
  });

  it('hides server exceptions behind Technical details', async () => {
    const raw = "MySQLdb.OperationalError: (1054, \"Unknown column 'phone_number_id' in 'SELECT'\")";
    render(<LoadError message={raw} />);
    expect(screen.getByText(/Something went wrong while loading this page/)).toBeInTheDocument();
    expect(screen.queryByText(raw)).toBeNull();
    await userEvent.click(screen.getByText('Technical details'));
    expect(screen.getByText(raw)).toBeInTheDocument();
  });
});
