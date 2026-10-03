import type { ComponentPropsWithoutRef, ElementType } from 'react'

type Props<T extends ElementType> = { as?: T } & ComponentPropsWithoutRef<T>

/** The prototype's frosted panel: `glass-panel` (app) or `glass-box` (public pages). */
export function GlassPanel<T extends ElementType = 'div'>({
  as,
  className = '',
  ...rest
}: Props<T>) {
  const Tag: ElementType = as ?? 'div'
  return <Tag className={`glass-panel rounded-2xl border border-gray-800 ${className}`} {...rest} />
}
