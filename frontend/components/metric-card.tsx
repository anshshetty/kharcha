import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** Shared financial summary. Formatting and accounting stay with the caller. */
export function MetricCard({
  label,
  value,
  icon: Icon,
  tone = 'neutral',
  children,
}: {
  label: string;
  value: string;
  icon: LucideIcon;
  tone?: 'neutral' | 'brand' | 'warm';
  children?: ReactNode;
}) {
  return (
    <div className={cn('metric-card', `metric-card--${tone}`)}>
      <div className="metric-label">
        <span>{label}</span>
        <span className="metric-icon">
          <Icon size={19} aria-hidden="true" />
        </span>
      </div>
      <strong className="metric-value">{value}</strong>
      <div className="metric-detail">{children}</div>
    </div>
  );
}
