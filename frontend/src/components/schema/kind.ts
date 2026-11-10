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
  /** Opens the question editor for a slot (P35): a new question (`questionId` null) or the
   * linked one. The page keeps the unsaved form and links the result on the way back. Absent
   * where the designer has no page behind it. */
  /** The form key of a slot to show opened: the one the SME just came back to from the editor. */
  reveal?: string | null
  openQuestion?: (request: { slotKey: string; questionId: string | null; marks: string }) => void
}

export const DesignerLinks = createContext<PaperLinks>({ subjectId: '', locked: false })

export function useDesignerLinks(): PaperLinks {
  return useContext(DesignerLinks)
}
