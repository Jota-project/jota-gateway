/**
 * Color parsing and conversion utilities.
 */
import { Options, SpecifiedColorChannels } from '../typedef';
/**
 * Parses an rgb() or rgba() CSS color function value.
 * @param value - The RGB color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color array ['rgb', r, g, b, alpha], an empty string, or null.
 */
export declare const parseRgb: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Parses an hsl() or hsla() CSS color function value.
 * @param value - The HSL color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color array ['rgb', r, g, b, alpha] (or formatted channels), an empty string, or null.
 */
export declare const parseHsl: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Parses an hwb() CSS color function value.
 * @param value - The HWB color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color array ['rgb', r, g, b, alpha] (or formatted channels), an empty string, or null.
 */
export declare const parseHwb: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Parses a lab() CSS color function value.
 * @param value - The lab color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-d50', x, y, z, alpha] or ['lab', l, a, b, alpha], an empty string, or null.
 */
export declare const parseLab: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Parses an lch() CSS color function value.
 * @param value - The lch color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-d50', x, y, z, alpha] or ['lch', l, c, h, alpha], an empty string, or null.
 */
export declare const parseLch: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Parses an oklab() CSS color function value.
 * @param value - The oklab color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-d65', x, y, z, alpha] or ['oklab', l, a, b, alpha], an empty string, or null.
 */
export declare const parseOklab: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Parses an oklch() CSS color function value.
 * @param value - The oklch color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-d65', x, y, z, alpha] or ['oklch', l, c, h, alpha], an empty string, or null.
 */
export declare const parseOklch: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Parses a generic color() CSS function value.
 * @param value - The color function value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-(d50|d65)', x, y, z, alpha] or [cs, r, g, b, alpha], an empty string, or null.
 */
export declare const parseColorFunc: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
/**
 * Parses a CSS color value string of any supported format.
 * @param value - The CSS color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color array/channels, an empty string, or null.
 */
export declare const parseColorValue: (value: string, opt?: Options) => SpecifiedColorChannels | string | null;
