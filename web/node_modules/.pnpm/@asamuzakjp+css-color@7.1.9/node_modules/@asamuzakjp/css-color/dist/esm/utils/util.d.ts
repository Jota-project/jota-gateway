/**
 * Utility functions.
 */
import { ColorData } from '@csstools/css-color-parser';
import { HueArc, Options, SpecifiedColorChannels } from '../typedef';
/**
 * Gets the maximum length allowed based on options and extension flag.
 * @param [opt] - Configuration options
 * @param [extend] - Whether to extend the maximum length limit
 * @returns The maximum length value
 */
export declare const getMaxLength: (opt?: Options, extend?: boolean) => number;
/**
 * Splits a CSS value string by a delimiter.
 * NOTE: Comments are stripped by default, but can be preserved if the delimiter is ',' or '/' and `preserveComment` is set to `true`.
 * @param value - The CSS value string to split
 * @param [opt] - Configuration options
 * @returns An array of split value strings
 */
export declare const splitValue: (value: string, opt?: Options) => string[];
/**
 * Extracts dashed-ident tokens from a CSS value string.
 * @param value - The CSS value string to inspect
 * @returns An array of unique dashed-ident tokens
 */
export declare const extractDashedIdent: (value: string) => string[];
/**
 * Rounds a numeric value to a specified precision bit level.
 * @param value - The numeric value to round
 * @param bit - The precision bit configuration
 * @returns The rounded numeric value
 */
export declare const roundToPrecision: (value: number, bit?: number) => number;
/**
 * Interpolates between two hue values based on the specified arc direction.
 * @param hueA - The first hue value
 * @param hueB - The second hue value
 * @param arc - The arc interpolation method ('shorter' | 'longer' | 'increasing' | 'decreasing')
 * @returns A tuple containing the adjusted [hueA, hueB] values
 */
export declare const interpolateHue: (hueA: number, hueB: number, arc?: HueArc | string) => [number, number];
/**
 * Resolves a length or font size to its equivalent value in pixels.
 * @param value - The numeric value or string identifier to resolve
 * @param unit - The unit of the length
 * @param [opt] - Configuration options
 * @returns The resolved pixel value, or NaN on failure
 */
export declare const resolveLengthInPixels: (value: number | string, unit: string | undefined, opt?: Options) => number;
/**
 * Converts an angle string to degrees (normalized between 0 and 360).
 * @param value - The angle string to convert
 * @returns The resolved degree value (0 to 360)
 */
export declare const angleToDeg: (value: string) => number;
/**
 * Converts a numeric value to a two-digit hexadecimal string.
 * @param value - The numeric value to convert
 * @returns The resulting hexadecimal string
 */
export declare const numberToHexString: (value: number) => string;
/**
 * Parses an alpha value string into a normalized number between 0 and 1.
 * @param [value] - The alpha value string to parse
 * @returns The normalized alpha value (0 to 1)
 */
export declare const parseAlpha: (value?: string) => number;
/**
 * Parses a hexadecimal alpha string into a normalized number between 0 and 1.
 * @param value - The hexadecimal alpha string to parse
 * @returns The normalized alpha value (0 to 1)
 */
export declare const parseHexAlpha: (value: string) => number;
/**
 * Parses a CSS color string generically into color data.
 * @param value - The CSS color value string
 * @returns The parsed color data object, or null if invalid
 */
export declare const parseColorGeneric: (value: string) => ColorData | null;
/**
 * Caches an invalid color value based on the given format and nullability.
 * @param cacheKey - The cache key
 * @param format - The output format
 * @param nullable - Whether the value can be null
 * @returns The cached default/fallback value
 */
export declare const cacheInvalidColorValue: (cacheKey: string, format: string, nullable?: boolean) => SpecifiedColorChannels | string | null;
/**
 * Resolves an invalid color value according to the output format and nullability.
 * @param format - The output format
 * @param nullable - Whether the value can be null
 * @returns The resolved fallback value
 */
export declare const resolveInvalidColorValue: (format: string, nullable?: boolean) => SpecifiedColorChannels | string | null;
/**
 * Parses a CSS string into an array of component parts.
 * @param css - The CSS string to parse
 * @returns An array of component part strings
 */
export declare const parseCalcComponents: (css: string) => string[];
