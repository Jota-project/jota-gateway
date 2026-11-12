/**
 * Color conversion utilities.
 */
import { ColorChannels, Options } from '../typedef';
/**
 * Pre-processes, resolves, and normalizes a CSS color value.
 * @param value - The CSS color value to pre-process.
 * @param opt - The conversion options.
 * @returns The resolved color string, or null if invalid.
 */
export declare const preProcess: (value: string, opt: Options) => string | null;
/**
 * Converts a numeric value to a two-digit hexadecimal string.
 * @param value - The numeric value to convert.
 * @returns The hexadecimal string representation (00..ff).
 */
export declare const numberToHex: (value: number) => string;
/**
 * Converts a CSS color value to a hexadecimal color string.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @param [opt.alpha] - Whether to enable the alpha channel in the output.
 * @returns The hexadecimal color string (#rrggbb | #rrggbbaa), or null if invalid.
 */
export declare const colorToHex: (value: string, opt?: Options) => string | null;
/**
 * Converts a CSS color value to HSL color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [h, s, l, alpha].
 */
export declare const colorToHsl: (value: string, opt?: Options) => ColorChannels;
/**
 * Converts a CSS color value to HWB color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [h, w, b, alpha].
 */
export declare const colorToHwb: (value: string, opt?: Options) => ColorChannels;
/**
 * Converts a CSS color value to CIELAB color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [l, a, b, alpha].
 */
export declare const colorToLab: (value: string, opt?: Options) => ColorChannels;
/**
 * Converts a CSS color value to CIELCH color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [l, c, h, alpha].
 */
export declare const colorToLch: (value: string, opt?: Options) => ColorChannels;
/**
 * Converts a CSS color value to Oklab color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [l, a, b, alpha].
 */
export declare const colorToOklab: (value: string, opt?: Options) => ColorChannels;
/**
 * Converts a CSS color value to Oklch color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [l, c, h, alpha].
 */
export declare const colorToOklch: (value: string, opt?: Options) => ColorChannels;
/**
 * Converts a CSS color value to sRGB color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [r, g, b, alpha].
 */
export declare const colorToRgb: (value: string, opt?: Options) => ColorChannels;
/**
 * Converts a CSS color value to XYZ color channels (D65).
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [x, y, z, alpha].
 */
export declare const colorToXyz: (value: string, opt?: Options) => ColorChannels;
/**
 * Converts a CSS color value to XYZ-D50 color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [x, y, z, alpha].
 */
export declare const colorToXyzD50: (value: string, opt?: Options) => ColorChannels;
export declare const convert: {
    colorToHex: (value: string, opt?: Options) => string | null;
    colorToHsl: (value: string, opt?: Options) => ColorChannels;
    colorToHwb: (value: string, opt?: Options) => ColorChannels;
    colorToLab: (value: string, opt?: Options) => ColorChannels;
    colorToLch: (value: string, opt?: Options) => ColorChannels;
    colorToOklab: (value: string, opt?: Options) => ColorChannels;
    colorToOklch: (value: string, opt?: Options) => ColorChannels;
    colorToRgb: (value: string, opt?: Options) => ColorChannels;
    colorToXyz: (value: string, opt?: Options) => ColorChannels;
    colorToXyzD50: (value: string, opt?: Options) => ColorChannels;
    numberToHex: (value: number) => string;
};
