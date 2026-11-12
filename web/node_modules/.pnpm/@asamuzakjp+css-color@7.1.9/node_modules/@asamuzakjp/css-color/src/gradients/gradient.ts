/**
 * CSS gradient resolution and validation utilities.
 */

import { Options } from '../typedef';
import { getMaxLength } from '../utils/util';
import { parseGradient } from './parse-gradient';

/* Constants */
import { NONE, VAL_SPEC } from '../utils/constant';

/**
 * Resolves and formats a CSS gradient string.
 * @param value - The CSS gradient value to resolve.
 * @param [opt] - The conversion options.
 * @returns The resolved gradient string, or a fallback value based on options.
 */
export const resolveGradient = (value: string, opt: Options = {}): string => {
  const options = {
    ...opt
  };
  const maxLength = getMaxLength(options);
  if (value && value.length <= maxLength) {
    const gradient = parseGradient(value, options);
    if (gradient) {
      const { type, gradientLine, colorStopList } = gradient;
      if (gradientLine) {
        return `${type}(${gradientLine}, ${colorStopList.join(', ')})`;
      }
      return `${type}(${colorStopList.join(', ')})`;
    }
  }
  return options.format === VAL_SPEC ? '' : NONE;
};

/**
 * Checks whether a given value is a valid CSS gradient.
 * @param value - The value to check.
 * @param [opt] - The validation options.
 * @returns True if the value is a valid CSS gradient, otherwise false.
 */
export const isGradient = (value: string, opt: Options = {}): boolean => {
  const options = {
    ...opt
  };
  return !!parseGradient(value, options);
};
