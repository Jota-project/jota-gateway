/**
 * Hexadecimal color conversion utilities.
 */
import { ColorChannels } from '../typedef';
/**
 * Converts sRGB color channels to a hexadecimal color string.
 * @param rgb - The color channels array: [r, g, b, alpha] (where r, g, b range from 0 to 255, and alpha ranges from 0 to 1).
 * @returns The resulting hexadecimal color string.
 */
export declare const convertRgbToHex: (rgb: ColorChannels) => string;
/**
 * Converts linear sRGB color channels to a hexadecimal color string.
 * @param rgb - The color channels array: [r, g, b, alpha] (where r, g, b, alpha range from 0 to 1).
 * @param [skip] - Whether to skip validation.
 * @returns The resulting hexadecimal color string.
 */
export declare const convertLinearRgbToHex: (rgb: ColorChannels, skip?: boolean) => string;
/**
 * Converts XYZ color channels (D65) to a hexadecimal color string.
 * @param xyz - The color channels array: [x, y, z, alpha].
 * @returns The resulting hexadecimal color string.
 */
export declare const convertXyzToHex: (xyz: ColorChannels) => string;
/**
 * Converts XYZ-D50 color channels to a hexadecimal color string.
 * @param xyz - The color channels array: [x, y, z, alpha].
 * @returns The resulting hexadecimal color string.
 */
export declare const convertXyzD50ToHex: (xyz: ColorChannels) => string;
/**
 * Converts a hexadecimal color string to sRGB color channels.
 * @param value - The hexadecimal color string to convert.
 * @returns The color channels array: [r, g, b, alpha] (where r, g, b range from 0 to 255, and alpha ranges from 0 to 1).
 */
export declare const convertHexToRgb: (value: string) => ColorChannels;
/**
 * Converts a hexadecimal color string to linear sRGB color channels.
 * @param value - The hexadecimal color string to convert.
 * @returns The color channels array: [r, g, b, alpha] (where r, g, b, alpha range from 0 to 1).
 */
export declare const convertHexToLinearRgb: (value: string) => ColorChannels;
/**
 * Converts a hexadecimal color string to XYZ color channels.
 * @param value - The hexadecimal color string to convert.
 * @returns The color channels array: [x, y, z, alpha].
 */
export declare const convertHexToXyz: (value: string) => ColorChannels;
