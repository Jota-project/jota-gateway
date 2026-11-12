/**
 * CSS gradient resolution and validation utilities.
 */
import { Options } from '../typedef';
/**
 * Resolves and formats a CSS gradient string.
 * @param value - The CSS gradient value to resolve.
 * @param [opt] - The conversion options.
 * @returns The resolved gradient string, or a fallback value based on options.
 */
export declare const resolveGradient: (value: string, opt?: Options) => string;
/**
 * Checks whether a given value is a valid CSS gradient.
 * @param value - The value to check.
 * @param [opt] - The validation options.
 * @returns True if the value is a valid CSS gradient, otherwise false.
 */
export declare const isGradient: (value: string, opt?: Options) => boolean;
