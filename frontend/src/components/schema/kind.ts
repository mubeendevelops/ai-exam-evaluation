import { createContext, useContext } from 'react'
import type { Kind } from '../../lib/blueprint'

/** The designer's mode: a schema shows no question links (D219); a paper does. */
export const DesignerKind = createContext<Kind>('paper')

export function useDesignerKind(): Kind {
  return useContext(DesignerKind)
}
