/** RAGOH form schema, produced on the server from XLSForm (backend/app/services/forms.py). */

export type FieldType =
  | "text"
  | "integer"
  | "decimal"
  | "select_one"
  | "select_multiple"
  | "date"
  | "time"
  | "datetime"
  | "geopoint"
  | "image"
  | "barcode"
  | "note"
  | "acknowledge"
  | "calculate"
  | "group"
  | "repeat";

export interface Choice {
  name: string;
  label: string;
  /** Extra columns from the choices sheet (used by choice_filter). */
  [extra: string]: unknown;
}

export interface Field {
  type: FieldType;
  name: string;
  label?: string;
  hint?: string;
  required?: string;
  required_message?: string;
  relevant?: string;
  constraint?: string;
  constraint_message?: string;
  calculation?: string;
  read_only?: string;
  default?: string;
  appearance?: string;
  choices?: Choice[];
  choice_filter?: string;
  children?: Field[];
  repeat_count?: string;
}

export interface FormSchema {
  schema_version: number;
  id_string: string;
  version: string;
  title: string;
  fields: Field[];
}

/** A geopoint answer. */
export interface GeoValue {
  lat: number;
  lon: number;
  accuracy_m?: number;
}

export type Scalar = string | number | boolean | null;
export type AnswerValue = Scalar | GeoValue | RepeatInstance[];
export interface RepeatInstance {
  [name: string]: AnswerValue | undefined;
}
/**
 * Answers keyed by field name. Groups are transparent (their children live at the same
 * level, as in ODK names); repeats hold an array of instances.
 * select_multiple answers are space-separated strings ("fever cough"), as in ODK.
 */
export type Answers = RepeatInstance;
