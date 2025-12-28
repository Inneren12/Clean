# UI Contract: Chat as a Form (S2-A)

## Overview

This document defines the optional UI contract extensions for the chat interface that enable form-like behavior with guided steps, selectable choices, and editable summaries.

**Key Principles:**
- All fields are **optional** and **backward-compatible**
- Plain text responses continue to work exactly as before
- Frontend gracefully handles presence/absence of new fields
- No breaking changes to existing API contracts

## Contract Extension Fields

The `ChatTurnResponse` model includes four new optional fields:

### 1. `choices` (ChoicesConfig)

Provides selectable options rendered as chips or buttons.

**Structure:**
```typescript
{
  items: Array<{
    id: string;              // Unique identifier
    label: string;           // Display text
    value?: string | null;   // Value to send (defaults to label)
  }>;
  multi_select?: boolean;    // Allow multiple selections (default: false)
  selection_type?: 'button' | 'chip';  // UI style hint (default: 'chip')
}
```

**Use Cases:**
- Yes/No questions
- Multiple choice selections
- Predefined options (cleaning types, frequencies, etc.)

---

### 2. `step_info` (StepInfo)

Displays conversation progress like a multi-step form.

**Structure:**
```typescript
{
  current_step: number;           // Current step (1-indexed)
  total_steps: number;            // Total number of steps
  step_label?: string | null;     // Custom label (e.g., "Property Details")
  remaining_questions?: number | null;  // Estimated questions left
}
```

**Use Cases:**
- Show progress through quote flow
- Estimate remaining questions
- Guide user expectations

---

### 3. `summary_patch` (SummaryPatch)

Structured summary with editable fields for review and correction.

**Structure:**
```typescript
{
  title?: string | null;         // Summary section title (default: "Conversation Summary")
  fields: Array<{
    key: string;                 // Field identifier (e.g., "beds", "cleaning_type")
    label: string;               // Display label (e.g., "Bedrooms")
    value: any;                  // Current value
    editable?: boolean;          // User can edit (default: true)
    field_type?: 'text' | 'number' | 'select' | 'boolean';  // Input type (default: 'text')
    options?: Array<Choice> | null;  // For 'select' type fields
  }>;
}
```

**Use Cases:**
- Review collected information
- Quick corrections without restarting
- Confirmation before booking

---

### 4. `ui_hint` (UIHint)

Hints for frontend rendering behavior.

**Structure:**
```typescript
{
  show_summary?: boolean;      // Display summary panel (default: false)
  show_confirm?: boolean;      // Show confirmation button (default: false)
  show_choices?: boolean;      // Render choices if available (default: false)
  show_progress?: boolean;     // Display step progress (default: false)
  minimize_text?: boolean;     // De-emphasize text for structured UI (default: false)
}
```

**Note:** Frontend uses sensible defaults (shows UI elements if data is present). Use `ui_hint` to override default behavior.

---

## Payload Examples

### Example 1: Plain Text (Backward Compatible)

**Response:**
```json
{
  "session_id": "abc-123",
  "intent": "QUOTE",
  "reply_text": "How many bedrooms does your home have?",
  "proposed_questions": ["1 bedroom", "2 bedrooms", "3 bedrooms"],
  "estimate": null,
  "choices": null,
  "step_info": null,
  "summary_patch": null,
  "ui_hint": null
}
```

**Rendering:** Standard text message with proposed questions as quick replies.

---

### Example 2: Single-Select Choices

**Response:**
```json
{
  "session_id": "abc-123",
  "intent": "QUOTE",
  "reply_text": "What type of cleaning do you need?",
  "choices": {
    "items": [
      {"id": "regular", "label": "Regular Cleaning"},
      {"id": "deep", "label": "Deep Cleaning"},
      {"id": "moveout", "label": "Move-Out Cleaning"}
    ],
    "multi_select": false,
    "selection_type": "chip"
  },
  "ui_hint": {
    "show_choices": true
  }
}
```

**Rendering:** Text message + chip-style selection buttons. Clicking a choice auto-submits.

---

### Example 3: Multi-Select Choices

**Response:**
```json
{
  "session_id": "abc-123",
  "intent": "QUOTE",
  "reply_text": "Select any add-ons you'd like:",
  "choices": {
    "items": [
      {"id": "oven", "label": "Inside Oven", "value": "inside oven"},
      {"id": "fridge", "label": "Inside Fridge", "value": "inside fridge"},
      {"id": "windows", "label": "Interior Windows", "value": "interior windows"}
    ],
    "multi_select": true,
    "selection_type": "chip"
  },
  "ui_hint": {
    "show_choices": true
  }
}
```

**Rendering:** Text + multi-select chips + "Confirm Selection" button that submits comma-separated values.

---

### Example 4: Step Progress

**Response:**
```json
{
  "session_id": "abc-123",
  "intent": "QUOTE",
  "reply_text": "How many bathrooms?",
  "step_info": {
    "current_step": 2,
    "total_steps": 5,
    "step_label": "Property Details",
    "remaining_questions": 3
  },
  "ui_hint": {
    "show_progress": true
  }
}
```

**Rendering:** Progress bar showing 40% complete (2/5) with "Property Details" label and "3 questions remaining".

---

### Example 5: Editable Summary

**Response:**
```json
{
  "session_id": "abc-123",
  "intent": "QUOTE",
  "reply_text": "Here's what I've collected so far. Please review:",
  "summary_patch": {
    "title": "Your Cleaning Details",
    "fields": [
      {
        "key": "beds",
        "label": "Bedrooms",
        "value": 2,
        "editable": true,
        "field_type": "number"
      },
      {
        "key": "baths",
        "label": "Bathrooms",
        "value": 1.5,
        "editable": true,
        "field_type": "number"
      },
      {
        "key": "cleaning_type",
        "label": "Cleaning Type",
        "value": "REGULAR",
        "editable": true,
        "field_type": "select",
        "options": [
          {"id": "regular", "label": "Regular Cleaning", "value": "REGULAR"},
          {"id": "deep", "label": "Deep Cleaning", "value": "DEEP"}
        ]
      },
      {
        "key": "heavy_grease",
        "label": "Heavy Grease",
        "value": false,
        "editable": true,
        "field_type": "boolean"
      }
    ]
  },
  "ui_hint": {
    "show_summary": true,
    "show_confirm": true
  }
}
```

**Rendering:** Summary panel with editable fields. Changing a field sends update message. "Confirm Details" button proceeds.

---

### Example 6: Combined (Choices + Progress)

**Response:**
```json
{
  "session_id": "abc-123",
  "intent": "QUOTE",
  "reply_text": "Do you need any furniture steam cleaning?",
  "choices": {
    "items": [
      {"id": "yes", "label": "Yes, add furniture steaming"},
      {"id": "no", "label": "No, skip this"}
    ],
    "multi_select": false,
    "selection_type": "button"
  },
  "step_info": {
    "current_step": 4,
    "total_steps": 5,
    "step_label": "Add-Ons",
    "remaining_questions": 1
  },
  "ui_hint": {
    "show_choices": true,
    "show_progress": true
  }
}
```

**Rendering:** Progress bar (80% complete) + text + button-style choices.

---

## Implementation Notes

### Backend

1. All new fields are optional in `ChatTurnResponse` (app/domain/chat/models.py)
2. Existing response builders continue to work (only `reply_text` required)
3. New fields can be added incrementally to conversation flows

### Frontend

1. All new fields have null-safe rendering (web/app/page.tsx)
2. If field is null/undefined, UI element is not rendered
3. Backward compatibility: plain text responses render exactly as before
4. State management: new fields stored separately, cleared on new bot message

### Testing Backward Compatibility

To verify backward compatibility:

```bash
# Test plain text response (no new fields)
curl -X POST http://localhost:8000/v1/chat/turn \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-123",
    "message": "I need a quote",
    "brand": "economy"
  }'

# Response should include reply_text and omit new fields
# Frontend should render normally without errors
```

## Future Extensions

Potential additions (not implemented):
- `choices.allow_custom_input`: Show text field alongside choices
- `summary_patch.patch_ops`: JSON Patch operations for efficient updates
- `ui_hint.input_type`: Suggest specific input methods (voice, calendar picker, etc.)

---

## Questions?

See implementation:
- Backend models: `app/domain/chat/models.py:43-107`
- Frontend types: `web/app/page.tsx:43-95`
- Frontend rendering: `web/app/page.tsx:708-941`
