import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { SyncIndicator } from './SyncIndicator';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

it('reads "Synced n ago" when not fetching', () => {
  // The copy moved with the component: it sits in the purple app header now,
  // where the design gives it one string and no status dot.
  render(<SyncIndicator dataUpdatedAt={Date.now() - 5000} isFetching={false} />);
  expect(screen.getByText('Synced 5s ago')).toBeInTheDocument();
});

it('no longer claims a subject count — the toolbar count pill owns that', () => {
  render(<SyncIndicator dataUpdatedAt={Date.now()} isFetching={false} />);
  expect(screen.queryByText(/subjects/)).not.toBeInTheDocument();
});

it('shows Syncing when isFetching is true', () => {
  render(<SyncIndicator dataUpdatedAt={Date.now()} isFetching={true} />);
  expect(screen.getByText(/Syncing/)).toBeInTheDocument();
});

describe('relative time formatting', () => {
  it('shows seconds when < 60s ago', () => {
    render(<SyncIndicator dataUpdatedAt={Date.now() - 14_000} isFetching={false} />);
    expect(screen.getByText(/14s ago/)).toBeInTheDocument();
  });

  it('shows minutes when >= 60s ago', () => {
    render(<SyncIndicator dataUpdatedAt={Date.now() - 90_000} isFetching={false} />);
    expect(screen.getByText(/1m ago/)).toBeInTheDocument();
  });

  it('increments the counter each second', () => {
    render(<SyncIndicator dataUpdatedAt={Date.now() - 10_000} isFetching={false} />);
    expect(screen.getByText(/10s ago/)).toBeInTheDocument();

    act(() => { vi.advanceTimersByTime(3000); });
    expect(screen.getByText(/13s ago/)).toBeInTheDocument();
  });
});

describe('never synced', () => {
  /**
   * `dataUpdatedAt` is 0 until the first SUCCESSFUL fetch. That is TanStack
   * Query's sentinel for "never", and read as a timestamp it is the epoch —
   * so a failed load rendered "Synced 498000h ago" in the header, directly
   * above the panel telling the reader the registry could not be reached.
   */
  it('says nothing rather than dating the sync to the epoch', () => {
    const { container } = render(<SyncIndicator dataUpdatedAt={0} isFetching={false} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/Synced/)).not.toBeInTheDocument();
    expect(screen.queryByText(/h ago/)).not.toBeInTheDocument();
  });

  it('still says Syncing on the first load, before there is anything to date', () => {
    render(<SyncIndicator dataUpdatedAt={0} isFetching={true} />);
    expect(screen.getByText(/Syncing/)).toBeInTheDocument();
  });
});
