/**
 * CSS color resolution and processing utilities.
 */
import { Options, SpecifiedColorChannels } from '../typedef';
/**
 * Resolves a CSS color value into its specified or computed channel representation.
 * @param value - The CSS color value string to resolve.
 * @param opt - The resolution options.
 * @returns The resolved color channels array, a string representation, an empty string, or null.
 */
export declare const resolveColorValue: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Resolves a generic color() CSS function value.
 * @param value - The color function value string to resolve.
 * @param opt - The resolution options.
 * @returns The resolved color channels array, an empty string, or null.
 */
export declare const resolveColorFunc: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Resolves a CSS color string based on the given options.
 * @param value - The CSS color string to resolve.
 * @param opt - The resolution options.
 * @returns The resolved color string, or null if resolution fails.
 */
export declare const resolveColor: (value: string, opt?: Options) => string | null;
/**
 * Validates whether a given value is a recognizable and valid CSS color.
 * @param value - The value to validate.
 * @param opt - The resolution options.
 * @returns True if the value is a valid color, otherwise false.
 */
export declare const isValidColor: (value: unknown, opt?: Options) => boolean;
