// The canonical field-key vocabulary is an OPEN QUESTION until the backend
// Task 7 document-family schemas exist. Only keys known here are translated;
// any unknown key displays its raw value (we never invent a translation).
const FIELD_LABELS_VI: Record<string, string> = {
  requested_amount: "Số tiền đề nghị",
  purpose: "Mục đích vay vốn",
};

export function fieldLabelVi(fieldKey: string): string {
  return FIELD_LABELS_VI[fieldKey] ?? fieldKey;
}
