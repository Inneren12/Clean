'use client';

import React from 'react';

export interface AlwaysVisibleHandoffProps {
  onHandoff: () => void;
  label?: string;
  className?: string;
}

/**
 * AlwaysVisibleHandoff - Persistent "Call a human" button
 *
 * Fixed-position button that's always visible, allowing users to request
 * human assistance at any point in the flow.
 *
 * @example
 * ```tsx
 * <AlwaysVisibleHandoff
 *   onHandoff={() => handleHumanHandoff()}
 *   label="Speak to a person"
 * />
 * ```
 */
export default function AlwaysVisibleHandoff({
  onHandoff,
  label = 'Call a human',
  className = '',
}: AlwaysVisibleHandoffProps) {
  return (
    <div className={`handoff-container ${className}`}>
      <button
        type="button"
        className="handoff-button"
        onClick={onHandoff}
      >
        <svg
          className="handoff-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
        </svg>
        {label}
      </button>
    </div>
  );
}
