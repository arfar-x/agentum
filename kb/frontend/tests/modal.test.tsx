import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { Modal } from '../src/components/ui/Modal';

describe('Modal', () => {
  it('keeps focus in a field when the parent re-renders with a new onClose', async () => {
    // The console polls and re-renders its children; each render hands the
    // modal a fresh onClose. That must not pull focus out of an input.
    const view = render(
      <Modal title="Edit" onClose={() => {}}>
        <input aria-label="Title" />
      </Modal>,
    );
    const input = screen.getByLabelText('Title');
    await userEvent.click(input);
    expect(input).toHaveFocus();

    view.rerender(
      <Modal title="Edit" onClose={() => {}}>
        <input aria-label="Title" />
      </Modal>,
    );
    expect(screen.getByLabelText('Title')).toHaveFocus();
  });

  it('still closes on Escape with the latest onClose', async () => {
    const first = vi.fn();
    const second = vi.fn();
    const view = render(<Modal title="Edit" onClose={first}>x</Modal>);
    view.rerender(<Modal title="Edit" onClose={second}>x</Modal>);
    await userEvent.keyboard('{Escape}');
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
