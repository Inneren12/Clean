'use client';

import React from 'react';

export interface PrimaryCTAProps {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  loading?: boolean;
  icon?: React.ReactNode;
  className?: string;
}

/**
 * PrimaryCTA - Primary call-to-action button
 *
 * A prominent button for the main action on a screen (e.g., "Confirm", "Book Now").
 * Supports loading and disabled states.
 *
 * @example
 * ```tsx
 * <PrimaryCTA
 *   label="Confirm Booking"
 *   onClick={() => handleConfirm()}
 *   loading={isSubmitting}
 * />
 * ```
 */
export default function PrimaryCTA({
  label,
  onClick,
  disabled = false,
  loading = false,
  icon,
  className = '',
}: PrimaryCTAProps) {
  return (
    <button
      type="button"
      className={`btn-primary-cta ${className}`}
      onClick={onClick}
      disabled={disabled || loading}
    >
      {loading ? (
        <>
          <span className="spinner" />
          Loading...
        </>
      ) : (
        <>
          {icon && <span className="btn-icon">{icon}</span>}
          {label}
        </>
      )}
    </button>
  );
}
