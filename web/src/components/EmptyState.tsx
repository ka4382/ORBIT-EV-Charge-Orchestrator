import { Glyph } from './Glyph';

export function EmptyState({ text }: { text: string }) {
  return (
    <div className="empty">
      <Glyph name="orbit" size={56} />
      <p style={{ margin: 0 }}>{text}</p>
    </div>
  );
}
