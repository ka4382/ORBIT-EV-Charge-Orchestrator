import type { CSSProperties } from 'react';

export type IconName =
  | 'home'
  | 'car'
  | 'pin'
  | 'history'
  | 'calendar'
  | 'battery'
  | 'info'
  | 'menu'
  | 'pause'
  | 'play'
  | 'chevron-right'
  | 'chevron-up'
  | 'search'
  | 'filter'
  | 'close'
  | 'arrow-right';

interface Props {
  name: IconName;
  size?: number;
  className?: string;
  title?: string;
}

/** 32px brand icons rendered as a currentColor mask. */
export function Icon({ name, size = 20, className = '', title }: Props) {
  const style = {
    '--icon': `url(/brand/icons/${name}.svg)`,
    width: size,
    height: size,
  } as CSSProperties;
  return (
    <span
      className={`icon ${className}`}
      style={style}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    />
  );
}
