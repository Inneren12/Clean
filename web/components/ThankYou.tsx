'use client';

import React from 'react';

export interface ThankYouProps {
  title?: string;
  message: string;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}

/**
 * ThankYou - Success/confirmation screen
 *
 * Displays a thank you message after successful actions
 * (booking confirmation, lead submission, etc.)
 *
 * @example
 * ```tsx
 * <ThankYou
 *   title="Booking Confirmed!"
 *   message="We'll see you on Monday, Jan 15 at 2:00 PM"
 *   actions={<button>Add to Calendar</button>}
 * />
 * ```
 */
export default function ThankYou({
  title = 'Thank You!',
  message,
  icon,
  actions,
  className = '',
}: ThankYouProps) {
  return (
    <div className={`card ${className}`} style={{ textAlign: 'center', padding: '32px' }}>
      {icon && (
        <div style={{ marginBottom: '16px', display: 'flex', justifyContent: 'center' }}>
          {icon}
        </div>
      )}

      <h2 style={{ marginBottom: '12px', fontSize: '24px', fontWeight: 600 }}>
        {title}
      </h2>

      <p style={{ marginBottom: actions ? '24px' : '0', color: '#64748b', fontSize: '16px' }}>
        {message}
      </p>

      {actions && (
        <div style={{ display: 'flex', gap: '12px', justifyContent: 'center', flexWrap: 'wrap' }}>
          {actions}
        </div>
      )}
    </div>
  );
}
