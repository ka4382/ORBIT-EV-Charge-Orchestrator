import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SocBar } from '../components/SocBar';
import { socColor, socTone } from './soc';

describe('SoC thresholds', () => {
  it('maps SoC to sage / ember / danger', () => {
    expect(socTone(80)).toBe('ok');
    expect(socTone(20)).toBe('ok');
    expect(socTone(19.9)).toBe('warn');
    expect(socTone(15)).toBe('warn');
    expect(socTone(14.9)).toBe('critical');
    expect(socColor(50)).toBe('var(--sage)');
    expect(socColor(18)).toBe('var(--ember)');
    expect(socColor(5)).toBe('var(--danger)');
  });

  it('SocBar renders width and colour from SoC', () => {
    const { rerender } = render(<SocBar soc={72} />);
    expect(screen.getByTestId('socbar-fill')).toHaveStyle({ width: '72%' });
    expect(screen.getByRole('meter').getAttribute('style')).toContain('--soc-c: var(--sage)');
    rerender(<SocBar soc={12} />);
    expect(screen.getByRole('meter').getAttribute('style')).toContain('--soc-c: var(--danger)');
    expect(screen.getByText('12%')).toBeInTheDocument();
  });
});
