/**
 * Common utility functions.
 */
/**
 * Returns the internal JavaScript type name of the given object.
 * @param o - The object to check
 * @returns The string representation of the object's type
 */
export declare const getType: (o: unknown) => string;
/**
 * Checks whether the given value is a string.
 * @param o - The value to check
 * @returns True if the value is a string, false otherwise
 */
export declare const isString: (o: unknown) => o is string;
/**
 * Checks whether the given value is a string or a number.
 * @param o - The value to check
 * @returns True if the value is a string or a number, false otherwise
 */
export declare const isStringOrNumber: (o: unknown) => o is string | number;
