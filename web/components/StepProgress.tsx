'use client';

import React from 'react';

export interface StepProgressProps {
  currentStep: number;
  totalSteps: number;
  remaining?: number;
  className?: string;
}

/**
 * StepProgress - Step indicator with progress bar
 *
 * Shows "Step X/Y" with optional "Remaining N" count.
 * Includes a visual progress bar.
 *
 * @example
 * ```tsx
 * <StepProgress
 *   currentStep={2}
 *   totalSteps={5}
 *   remaining={3}
 * />
 * ```
 */
export default function StepProgress({
  currentStep,
  totalSteps,
  remaining,
  className = '',
}: StepProgressProps) {
  const progressPercent = (currentStep / totalSteps) * 100;

  return (
    <div className={`step-progress ${className}`}>
      <div className="step-progress-text">
        Step {currentStep} of {totalSteps}
        {remaining !== undefined && remaining > 0 && (
          <span className="step-progress-remaining"> • {remaining} remaining</span>
        )}
      </div>
      <div className="step-progress-bar">
        <div
          className="step-progress-fill"
          style={{ width: `${progressPercent}%` }}
        />
      </div>
    </div>
  );
}
