import { createContext, useContext } from 'react'
import type { Kind } from '../../lib/blueprint'

/** The designer's mode: a schema shows no question links (D219); a paper does. */
export const DesignerKind = createContext<Kind>('paper')

export function useDesignerKind(): Kind {
  return useContext(DesignerKind)
}

/** What the question picker needs (P34): the paper's subject, and whether its structure is
 * locked because booklets were uploaded against it (D222). */
export interface PaperLinks {
  subjectId: string
  locked: boolean
}

export const DesignerLinks = createContext<PaperLinks>({ subjectId: '', locked: false })

export function useDesignerLinks(): PaperLinks {
  return useContext(DesignerLinks)
}
