import { render, screen } from '@testing-library/react';
import App from './App';

test('renders app heading', () => {
  render(<App />);
  const heading = screen.getByText(/Maharashtra Legal Document Generation System/i);
  expect(heading).toBeInTheDocument();
});
