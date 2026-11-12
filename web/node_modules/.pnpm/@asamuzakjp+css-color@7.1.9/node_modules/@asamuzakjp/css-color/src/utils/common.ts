/**
 * Common utility functions.
 */

/* Numeric constants for extracting object types */
const TYPE_FROM = 8;
const TYPE_TO = -1;

/**
 * Returns the internal JavaScript type name of the given object.
 * @param o - The object to check
 * @returns The string representation of the object's type
 */
export const getType = (o: unknown): string =>
  Object.prototype.toString.call(o).slice(TYPE_FROM, TYPE_TO);

/**
 * Checks whether the given value is a string.
 * @param o - The value to check
 * @returns True if the value is a string, false otherwise
 */
export const isString = (o: unknown): o is string =>
  typeof o === 'string' || o instanceof String;

/**
 * Checks whether the given value is a string or a number.
 * @param o - The value to check
 * @returns True if the value is a string or a number, false otherwise
 */
export const isStringOrNumber = (o: unknown): o is string | number =>
  isString(o) || typeof o === 'number';
