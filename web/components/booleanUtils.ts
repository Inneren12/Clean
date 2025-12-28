export const normalizeBooleanValue = (value: unknown): boolean =>
  typeof value === 'boolean' ? value : false;

export const formatBooleanLabel = (value: unknown): string => {
  if (typeof value !== 'boolean') return '—';
  return value ? 'Yes' : 'No';
};
