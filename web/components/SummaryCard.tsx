'use client';

import React, { useState, useEffect } from 'react';

export interface SummaryFieldData {
  id: string;
  label: string;
  value: string | number | boolean | null;
  type: 'text' | 'number' | 'select' | 'boolean';
  options?: Array<{ value: string; label: string }>;
  editable?: boolean;
}

export interface SummaryCardProps {
  title: string;
  fields: SummaryFieldData[];
  onSave?: (updates: Record<string, any>) => void;
  className?: string;
  showActions?: boolean;
}

/**
 * SummaryCard - Editable summary display
 *
 * Shows "what the bot understood" with edit-in-place functionality.
 * Includes Apply/Save actions when in edit mode.
 *
 * @example
 * ```tsx
 * <SummaryCard
 *   title="Your Cleaning Details"
 *   fields={[
 *     { id: 'size', label: 'Home Size', value: 'Medium (3BR)', type: 'text' },
 *     { id: 'rooms', label: 'Bedrooms', value: 3, type: 'number', editable: true }
 *   ]}
 *   onSave={(updates) => console.log(updates)}
 * />
 * ```
 */
export default function SummaryCard({
  title,
  fields,
  onSave,
  className = '',
  showActions = true,
}: SummaryCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editedValues, setEditedValues] = useState<Record<string, any>>({});

  // Initialize edited values when entering edit mode
  useEffect(() => {
    if (isEditing) {
      const initialValues: Record<string, any> = {};
      fields.forEach(field => {
        if (field.editable) {
          initialValues[field.id] = field.value;
        }
      });
      setEditedValues(initialValues);
    }
  }, [isEditing, fields]);

  const hasEditableFields = fields.some(field => field.editable);

  const handleEdit = () => {
    setIsEditing(true);
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditedValues({});
  };

  const handleSave = () => {
    if (onSave) {
      onSave(editedValues);
    }
    setIsEditing(false);
  };

  const handleFieldChange = (fieldId: string, value: any) => {
    setEditedValues(prev => ({
      ...prev,
      [fieldId]: value,
    }));
  };

  const renderFieldValue = (field: SummaryFieldData) => {
    const currentValue = isEditing && field.editable
      ? editedValues[field.id]
      : field.value;

    if (isEditing && field.editable) {
      switch (field.type) {
        case 'text':
          return (
            <input
              type="text"
              className="summary-field-input"
              value={(currentValue ?? '') as string}
              onChange={(e) => handleFieldChange(field.id, e.target.value)}
            />
          );

        case 'number':
          return (
            <input
              type="number"
              className="summary-field-input"
              value={(currentValue ?? 0) as number}
              onChange={(e) => handleFieldChange(field.id, parseFloat(e.target.value))}
            />
          );

        case 'select':
          return (
            <select
              className="summary-field-input"
              value={(currentValue ?? '') as string}
              onChange={(e) => handleFieldChange(field.id, e.target.value)}
            >
              {field.options?.map(opt => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          );

        case 'boolean':
          return (
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <input
                type="checkbox"
                checked={(currentValue ?? false) as boolean}
                onChange={(e) => handleFieldChange(field.id, e.target.checked)}
              />
              <span className="summary-field-value">
                {currentValue ? 'Yes' : 'No'}
              </span>
            </label>
          );

        default:
          return <span className="summary-field-value">{String(currentValue ?? '—')}</span>;
      }
    }

    // Display mode - handle null/undefined
    if (currentValue === null || currentValue === undefined) {
      return <span className="summary-field-value">—</span>;
    }

    if (field.type === 'boolean') {
      return <span className="summary-field-value">{currentValue ? 'Yes' : 'No'}</span>;
    }

    return <span className="summary-field-value">{String(currentValue)}</span>;
  };

  return (
    <div className={`summary-card ${className}`}>
      <div className="summary-card-header">
        <h3 className="summary-card-title">{title}</h3>
        {hasEditableFields && showActions && !isEditing && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={handleEdit}
          >
            Edit
          </button>
        )}
      </div>

      <div className="summary-card-body">
        {fields.map((field) => (
          <div key={field.id} className="summary-field">
            <label className="summary-field-label">{field.label}</label>
            {renderFieldValue(field)}
          </div>
        ))}
      </div>

      {isEditing && showActions && (
        <div className="summary-card-footer">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSave}
          >
            Apply Changes
          </button>
        </div>
      )}
    </div>
  );
}
