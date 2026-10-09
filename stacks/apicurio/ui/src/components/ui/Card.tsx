import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      // 16px radius and 20px padding are the design's card geometry; `rounded`
      // would now resolve to the 4px meant for inputs and chips. `shadow-sm` is
      // the purple-tinted 0 1px 2px rgba(13,0,77,.06) rest shadow.
      className={cn(
        'bg-paper-card border border-paper-line rounded-2xl p-5 shadow-sm',
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('mb-4 flex items-center justify-between', className)} {...props} />;
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn('text-base font-semibold text-ink', className)} {...props} />;
}
