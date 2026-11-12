/**
 * CSS color resolution utilities.
 */
import { Options } from '../typedef';
/**
 * Resolves a CSS color value. System colors are not supported.
 * @param value - The CSS color value to resolve.
 * @param opt - The resolution options.
 * @returns The resolved color string, or null if resolution fails.
 */
export declare const resolve: (value: string, opt?: Options) => string | null;
/**
 * Checks whether a given value is a valid CSS color.
 * @param value - The value to check.
 * @param opt - The resolution options.
 * @returns True if the value is a valid color, otherwise false.
 */
export declare const isColor: (value: unknown, opt?: Options) => boolean;
