/**
 * CSS color resolution utilities.
 */

import { Options } from '../typedef';
import { isValidColor, resolveColor } from './resolve-color';

/**
 * Resolves a CSS color value. System colors are not supported.
 * @param value - The CSS color value to resolve.
 * @param opt - The resolution options.
 * @returns The resolved color string, or null if resolution fails.
 */
export const resolve = (value: string, opt: Options = {}): string | null => {
  const options = {
    ...opt
  };
  return resolveColor(value, options);
};

/**
 * Checks whether a given value is a valid CSS color.
 * @param value - The value to check.
 * @param opt - The resolution options.
 * @returns True if the value is a valid color, otherwise false.
 */
export const isColor = (value: unknown, opt: Options = {}): boolean => {
  const options = {
    ...opt,
    nullable: true
  };
  return !!isValidColor(value, options);
};
