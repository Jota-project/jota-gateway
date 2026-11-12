/**
 * Color conversion matrices and color space transformation constants.
 * @see https://drafts.csswg.org/css-color-4/#color-conversion-code
 */
import { ReadonlyColorMatrix, ReadonlyTriColorChannels } from '../typedef';
/**
 * D50 white point coordinates.
 */
export declare const D50: ReadonlyTriColorChannels;
/**
 * Matrix for converting from D50 to D65 white point.
 */
export declare const MATRIX_D50_TO_D65: ReadonlyColorMatrix;
/**
 * Matrix for converting from D65 to D50 white point.
 */
export declare const MATRIX_D65_TO_D50: ReadonlyColorMatrix;
/**
 * Matrix for converting from linear sRGB to XYZ color space.
 */
export declare const MATRIX_L_RGB_TO_XYZ: ReadonlyColorMatrix;
/**
 * Matrix for converting from XYZ to linear sRGB color space.
 */
export declare const MATRIX_XYZ_TO_L_RGB: ReadonlyColorMatrix;
/**
 * Matrix for converting from XYZ to LMS color space.
 */
export declare const MATRIX_XYZ_TO_LMS: ReadonlyColorMatrix;
/**
 * Matrix for converting from LMS to XYZ color space.
 */
export declare const MATRIX_LMS_TO_XYZ: ReadonlyColorMatrix;
/**
 * Matrix for converting from Oklab to LMS color space.
 */
export declare const MATRIX_OKLAB_TO_LMS: ReadonlyColorMatrix;
/**
 * Matrix for converting from LMS to Oklab color space.
 */
export declare const MATRIX_LMS_TO_OKLAB: ReadonlyColorMatrix;
/**
 * Matrix for converting from Display P3 to XYZ color space.
 */
export declare const MATRIX_P3_TO_XYZ: ReadonlyColorMatrix;
/**
 * Matrix for converting from Rec. 2020 to XYZ color space.
 */
export declare const MATRIX_REC2020_TO_XYZ: ReadonlyColorMatrix;
/**
 * Matrix for converting from Adobe RGB (1998) to XYZ color space.
 */
export declare const MATRIX_A98_TO_XYZ: ReadonlyColorMatrix;
/**
 * Matrix for converting from ProPhoto RGB to XYZ-D50 color space.
 */
export declare const MATRIX_PROPHOTO_TO_XYZ_D50: ReadonlyColorMatrix;
