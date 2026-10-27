import type { ReactNode } from 'react'
import { PageBanner } from '../../components/app/PageBanner'
import { EmptyState } from '../../components/ui'

/** A section of the admin portal that a later prompt builds: its banner and a short empty state. */
export default function ComingSoonPage({
  icon,
  iconTone,
  title,
  description,
  emptyTitle,
  emptyText,
  action,
}: {
  icon: string
  iconTone: string
  title: string
  description: string
  emptyTitle: string
  emptyText: string
  action?: ReactNode
}) {
  return (
    <div className="space-y-6">
      <PageBanner icon={icon} iconTone={iconTone} title={title} description={description} />
      <EmptyState icon={icon} title={emptyTitle} action={action}>
        {emptyText}
      </EmptyState>
    </div>
  )
}
