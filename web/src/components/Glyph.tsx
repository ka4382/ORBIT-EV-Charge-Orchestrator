export type GlyphName =
  'orbit' | 'converge' | 'sunbox' | 'prism' | 'toggle-spark' | 'brush-orange' | 'hand-rule';

interface Props {
  name: GlyphName;
  size?: number;
  tone?: 'auto' | 'sage' | 'ember' | 'raw';
  className?: string;
  alt?: string;
}

/** Agency glyphs (black on transparent). `auto` inverts on the dark theme. */
export function Glyph({ name, size = 48, tone = 'auto', className = '', alt = '' }: Props) {
  const toneClass =
    tone === 'sage' ? 'glyph-sage' : tone === 'ember' ? 'glyph-ember' : tone === 'raw' ? '' : '';
  return (
    <img
      src={`/brand/glyphs/${name}.svg`}
      width={size}
      height={size}
      alt={alt}
      className={`${tone === 'raw' ? '' : 'glyph'} ${toneClass} ${className}`}
      style={tone === 'raw' ? { width: size, height: size, objectFit: 'contain' } : undefined}
      draggable={false}
    />
  );
}
