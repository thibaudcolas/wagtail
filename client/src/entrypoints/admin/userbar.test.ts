import { AxeResults } from 'axe-core';
import { sortAxeViolations } from './userbar';

describe('sortAxeViolations', () => {
  it.todo('works with no violations');

  it.skip('works with no nodes', () => {
    const violations = [
      { id: 'axe-1', nodes: [] },
    ] as unknown as AxeResults['violations'];
    expect(sortAxeViolations(violations)).toBe(4);
  });

  it.todo('sorts nodes for one rule');

  it.todo('preserves the existing order if correct');

  it('changes the order to match the DOM', () => {
    document.body.innerHTML = `
      <div id="a"></div>
      <div id="b"></div>
      <div id="c"></div>
    `;
    const b = { id: 'axe-1', nodes: [{ target: ['#b'] }] };
    const a = { id: 'axe-2', nodes: [{ target: ['#a'] }] };
    const violations = [b, a] as unknown as AxeResults['violations'];
    expect(sortAxeViolations(violations)).toEqual([a, b]);
  });
});
