// src/lib/utils.ts
import { type ClassValue, clsx } from 'clsx'

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs)
}

export function statusColor(status: string) {
  switch (status) {
    case 'pending':
      return 'bg-status-pending/15 text-status-pending border-status-pending/30'
    case 'processing':
      return 'bg-status-processing/15 text-status-processing border-status-processing/30'
    case 'done':
      return 'bg-status-success/15 text-status-success border-status-success/30'
    case 'failed':
      return 'bg-status-error/15 text-status-error border-status-error/30'
    default:
      return 'bg-muted text-muted-foreground border-border'
  }
}
