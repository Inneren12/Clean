'use client';

import React from 'react';

export interface ChipOption {
  id: string;
  label: string;
  disabled?: boolean;
}

export interface QuickChipsProps {
  options: ChipOption[];
  mode: 'single' | 'multi';
  selected: string[];
  onChange: (selected: string[]) => void;
  className?: string;
}

/**
 * QuickChips - Reusable chip/button selector
 *
 * Supports both single and multi-select modes.
 * Used for presenting quick choice options to users.
 *
 * @example
 * ```tsx
 * <QuickChips
 *   options={[
 *     { id: 'small', label: 'Small (1-2BR)' },
 *     { id: 'medium', label: 'Medium (3BR)' }
 *   ]}
 *   mode="single"
 *   selected={['small']}
 *   onChange={(selected) => console.log(selected)}
 * />
 * ```
 */
export default function QuickChips({
  options,
  mode,
  selected,
  onChange,
  className = '',
}: QuickChipsProps) {
  const handleChipClick = (optionId: string) => {
    if (mode === 'single') {
      onChange([optionId]);
    } else {
      // Multi-select mode
      if (selected.includes(optionId)) {
        onChange(selected.filter(id => id !== optionId));
      } else {
        onChange([...selected, optionId]);
      }
    }
  };

  return (
    <div className={`chip-group ${className}`}>
      {options.map((option) => {
        const isSelected = selected.includes(option.id);
        return (
          <button
            key={option.id}
            type="button"
            className={`chip ${isSelected ? 'chip-selected' : ''}`}
            onClick={() => handleChipClick(option.id)}
            disabled={option.disabled}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
