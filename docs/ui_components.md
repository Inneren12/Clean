# UI Kit Components (S2-B)

Reusable UI components for the "chat as form" experience.

## Overview

This UI Kit provides a set of reusable, accessible components designed for conversational interfaces. All components follow a consistent design system and are built with TypeScript for type safety.

## Components

### 1. QuickChips

**Purpose:** Display choice options as chips/buttons with single or multi-select modes.

**Location:** `web/components/QuickChips.tsx`

**Props:**
- `options: ChipOption[]` - Array of chip options
  - `id: string` - Unique identifier
  - `label: string` - Display text
  - `disabled?: boolean` - Optional disabled state
- `mode: 'single' | 'multi'` - Selection mode
- `selected: string[]` - Array of selected chip IDs
- `onChange: (selected: string[]) => void` - Callback when selection changes
- `className?: string` - Optional additional CSS class

**Usage:**
```tsx
import { QuickChips } from '@/components';

<QuickChips
  options={[
    { id: 'small', label: 'Small (1-2BR)' },
    { id: 'medium', label: 'Medium (3BR)' },
    { id: 'large', label: 'Large (4BR+)' }
  ]}
  mode="single"
  selected={['medium']}
  onChange={(selected) => handleSelection(selected)}
/>
```

**Features:**
- Automatic submit on single-select
- Multi-select requires confirmation button
- Hover and selected states
- Disabled state support

---

### 2. SummaryCard

**Purpose:** Display editable summary with "what the bot understood" and Apply/Save functionality.

**Location:** `web/components/SummaryCard.tsx`

**Props:**
- `title: string` - Card title
- `fields: SummaryFieldData[]` - Array of summary fields
  - `id: string` - Field identifier
  - `label: string` - Field label
  - `value: string | number | boolean` - Current value
  - `type: 'text' | 'number' | 'select' | 'boolean'` - Input type
  - `options?: Array<{value: string, label: string}>` - Options for select type
  - `editable?: boolean` - Whether field can be edited
- `onSave?: (updates: Record<string, any>) => void` - Callback when changes are saved
- `className?: string` - Optional CSS class
- `showActions?: boolean` - Show edit/save buttons (default: true)

**Usage:**
```tsx
import { SummaryCard } from '@/components';

<SummaryCard
  title="Your Cleaning Details"
  fields={[
    { id: 'size', label: 'Home Size', value: 'Medium (3BR)', type: 'text' },
    { id: 'rooms', label: 'Bedrooms', value: 3, type: 'number', editable: true },
    { id: 'pets', label: 'Has Pets', value: true, type: 'boolean', editable: true }
  ]}
  onSave={(updates) => {
    console.log('Updates:', updates);
    // Send updates to backend
  }}
/>
```

**Features:**
- Edit mode with Apply/Cancel buttons
- Supports text, number, select, and boolean fields
- Batched updates (not individual field changes)
- Validation-ready

---

### 3. StepProgress

**Purpose:** Show progress through a multi-step flow with visual progress bar.

**Location:** `web/components/StepProgress.tsx`

**Props:**
- `currentStep: number` - Current step (1-based)
- `totalSteps: number` - Total number of steps
- `remaining?: number` - Optional remaining count
- `className?: string` - Optional CSS class

**Usage:**
```tsx
import { StepProgress } from '@/components';

<StepProgress
  currentStep={2}
  totalSteps={5}
  remaining={3}
/>
```

**Features:**
- Animated progress bar
- Optional "N remaining" display
- Responsive design
- Accessible markup

---

### 4. PrimaryCTA

**Purpose:** Main call-to-action button for primary actions.

**Location:** `web/components/PrimaryCTA.tsx`

**Props:**
- `label: string` - Button text
- `onClick: () => void` - Click handler
- `disabled?: boolean` - Disabled state
- `loading?: boolean` - Loading state with spinner
- `icon?: React.ReactNode` - Optional icon
- `className?: string` - Optional CSS class

**Usage:**
```tsx
import { PrimaryCTA } from '@/components';

<PrimaryCTA
  label="Confirm Booking"
  onClick={handleConfirm}
  loading={isSubmitting}
/>
```

**Features:**
- Loading state with spinner
- Hover animations
- Icon support
- Accessible button markup

---

### 5. AlwaysVisibleHandoff

**Purpose:** Fixed-position button for requesting human assistance, always visible.

**Location:** `web/components/AlwaysVisibleHandoff.tsx`

**Props:**
- `onHandoff: () => void` - Callback when handoff is requested
- `label?: string` - Button text (default: "Call a human")
- `className?: string` - Optional CSS class

**Usage:**
```tsx
import { AlwaysVisibleHandoff } from '@/components';

<AlwaysVisibleHandoff
  onHandoff={() => {
    // Handle human handoff
    sendMessage('I would like to speak with a human');
  }}
  label="Speak to a person"
/>
```

**Features:**
- Fixed positioning (bottom-right)
- Always visible (z-index: 1000)
- Phone icon
- Mobile responsive
- Hover animations

---

### 6. ThankYou

**Purpose:** Success/confirmation screen for completed actions.

**Location:** `web/components/ThankYou.tsx`

**Props:**
- `title?: string` - Title (default: "Thank You!")
- `message: string` - Success message
- `icon?: React.ReactNode` - Optional icon
- `actions?: React.ReactNode` - Optional action buttons
- `className?: string` - Optional CSS class

**Usage:**
```tsx
import { ThankYou } from '@/components';

<ThankYou
  title="Booking Confirmed!"
  message="We'll see you on Monday, Jan 15 at 2:00 PM"
  actions={
    <>
      <button className="btn btn-primary">Add to Calendar</button>
      <button className="btn btn-secondary">View Details</button>
    </>
  }
/>
```

**Features:**
- Centered layout
- Icon support
- Custom actions
- Card-based design

---

## Design System

### Spacing

```css
--space-xs: 4px
--space-sm: 8px
--space-md: 12px
--space-lg: 16px
--space-xl: 24px
--space-2xl: 32px
--space-3xl: 48px
```

### Typography

```css
--text-xs: 12px
--text-sm: 14px
--text-base: 16px
--text-lg: 18px
--text-xl: 20px
--text-2xl: 24px

--weight-normal: 400
--weight-medium: 500
--weight-semibold: 600
--weight-bold: 700
```

### Colors

Inherited from `globals.css`:
- `--primary`: Primary brand color
- `--primary-strong`: Darker primary
- `--primary-soft`: Lighter primary
- `--surface`: Card/surface background
- `--text`: Primary text color

---

## Integration Points

### Chat Screen (`web/app/page.tsx`)

Components are integrated into the main chat interface:

1. **StepProgress** - Shows progress through the conversation flow
   - Line: ~727
   - Renders when `stepInfo` is available

2. **QuickChips** - Displays choice options from the bot
   - Line: ~740
   - Used for single/multi-select choices
   - Connected to `PrimaryCTA` for multi-select confirmation

3. **SummaryCard** - Shows editable conversation summary
   - Line: ~870
   - Displays collected information
   - Allows batch editing with Apply/Save

4. **PrimaryCTA** - Primary action buttons
   - Used with QuickChips for multi-select confirmation
   - Used for "Confirm Details" action

5. **AlwaysVisibleHandoff** - Always-visible handoff button
   - Line: ~1156
   - Fixed position, always accessible
   - Triggers message: "I would like to speak with a human"

---

## Best Practices

### Component Reusability

✅ **DO:**
- Use components for any UI pattern that appears more than once
- Pass data and callbacks as props (no hard-coded logic)
- Keep components focused on presentation, not business logic

❌ **DON'T:**
- Hard-code values inside components
- Mix business logic with UI components
- Create one-off components for specific screens

### Styling

✅ **DO:**
- Use design system variables for consistency
- Add custom classes via `className` prop when needed
- Use inline styles sparingly (only for truly dynamic values)

❌ **DON'T:**
- Override component styles globally
- Use arbitrary spacing/color values
- Rely heavily on inline styles

### Accessibility

✅ **DO:**
- Use semantic HTML (button, label, etc.)
- Provide accessible labels
- Support keyboard navigation
- Test with screen readers

❌ **DON'T:**
- Use divs for interactive elements
- Forget focus states
- Hide important info from assistive tech

---

## Testing Checklist

- [ ] Components render without errors
- [ ] Single-select chips submit immediately
- [ ] Multi-select chips require confirmation
- [ ] SummaryCard edit mode works correctly
- [ ] SummaryCard batches updates
- [ ] StepProgress shows correct progress
- [ ] PrimaryCTA shows loading state
- [ ] AlwaysVisibleHandoff is always visible
- [ ] Mobile responsiveness works
- [ ] No regressions in existing flows

---

## Future Enhancements

Potential additions for future iterations:

1. **Form validation** - Add validation to SummaryCard fields
2. **Animations** - Add entry/exit animations to components
3. **Themes** - Support light/dark mode
4. **More input types** - Date pickers, time selectors, etc.
5. **Storybook** - Add component documentation and examples
6. **Unit tests** - Add Jest/React Testing Library tests

---

## Questions?

For component usage questions or issues:
- Check component JSDoc comments in source files
- Review examples in this document
- Test in isolation before integrating
- Refer to the design system tokens

---

**Last Updated:** 2025-12-28
**Version:** S2-B (UI Kit Components)
