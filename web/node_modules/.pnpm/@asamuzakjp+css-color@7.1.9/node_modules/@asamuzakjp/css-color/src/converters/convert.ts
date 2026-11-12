/**
 * Color conversion utilities.
 */

import { parseColorFunc, parseColorValue } from '../resolvers/parse-color';
import { resolveRelativeColor } from '../resolvers/relative-color';
import { resolveColor } from '../resolvers/resolve-color';
import {
  ColorChannels,
  ColorConvertFn,
  ComputedColorChannels,
  Options
} from '../typedef';
import { createCacheKey, getCache, setCache } from '../utils/cache';
import { isString } from '../utils/common';
import { cssCalc, resolveVar } from '../utils/css-calc-var';
import { getMaxLength, numberToHexString } from '../utils/util';
import {
  convertColorToHsl,
  convertColorToHwb,
  convertColorToLab,
  convertColorToLch,
  convertColorToOklab,
  convertColorToOklch,
  convertColorToRgb
} from './convert-color';

/* Constants */
import {
  SYN_FN_CALC,
  SYN_FN_REL,
  SYN_FN_VAR,
  VAL_COMP
} from '../utils/constant';
const NAMESPACE = 'convert';

/* Regular expressions */
const REG_FN_CALC = new RegExp(SYN_FN_CALC);
const REG_FN_REL = new RegExp(SYN_FN_REL);
const REG_FN_VAR = new RegExp(SYN_FN_VAR);

/**
 * Pre-processes, resolves, and normalizes a CSS color value.
 * @param value - The CSS color value to pre-process.
 * @param opt - The conversion options.
 * @returns The resolved color string, or null if invalid.
 */
export const preProcess = (value: string, opt: Options): string | null => {
  if (!isString(value)) {
    return null;
  }
  value = value.trim();
  const maxLength = getMaxLength(opt);
  if (!value || value.length > maxLength) {
    return null;
  }
  const cacheKey: string = createCacheKey(
    { namespace: NAMESPACE, name: 'preProcess', value },
    opt
  );
  const cachedResult = getCache(cacheKey);
  if (cachedResult !== false) {
    return cachedResult.item as string | null;
  }
  let res: string | null = value;
  if (REG_FN_VAR.test(value)) {
    const resolved = resolveVar(value, opt);
    if (!isString(resolved)) {
      setCache(cacheKey, null);
      return null;
    }
    res = resolved;
  }
  if (REG_FN_REL.test(res)) {
    const resolved = resolveRelativeColor(res, opt);
    if (!isString(resolved)) {
      setCache(cacheKey, null);
      return null;
    }
    res = resolved;
  } else if (REG_FN_CALC.test(res)) {
    res = cssCalc(res, opt);
  }
  if (res.startsWith('color-mix')) {
    const resolvedColorMix = resolveColor(res, {
      ...opt,
      format: VAL_COMP,
      nullable: true
    });
    res = typeof resolvedColorMix === 'string' ? resolvedColorMix : null;
  }
  if (!res || res.length > maxLength) {
    setCache(cacheKey, null);
    return null;
  }
  setCache(cacheKey, res);
  return res;
};

/**
 * Factory function to create a cached color converter.
 * @param name - The function name used for caching.
 * @param format - The target color format.
 * @param convertFn - The underlying conversion function.
 * @returns A color converter function.
 */
const createColorConverter = (
  name: string,
  format: string,
  convertFn: ColorConvertFn
) => {
  return (value: string, opt: Options = {}): ColorChannels => {
    if (!isString(value)) {
      throw new TypeError(`${value} is not a string.`);
    }
    const options = {
      ...opt
    };
    const resolved = preProcess(value, options);
    if (resolved === null) {
      return [0, 0, 0, 0];
    }
    const val = resolved.toLowerCase();
    const cacheKey = createCacheKey(
      { namespace: NAMESPACE, name, value: val },
      options
    );
    const cached = getCache(cacheKey);
    if (cached !== false) {
      return [...(cached.item as ColorChannels)] as ColorChannels;
    }
    const result = convertFn(val, { ...options, format }) as ColorChannels;
    setCache(cacheKey, result);
    return [...result];
  };
};

/**
 * Converts a numeric value to a two-digit hexadecimal string.
 * @param value - The numeric value to convert.
 * @returns The hexadecimal string representation (00..ff).
 */
export const numberToHex = (value: number): string => numberToHexString(value);

/**
 * Converts a CSS color value to a hexadecimal color string.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @param [opt.alpha] - Whether to enable the alpha channel in the output.
 * @returns The hexadecimal color string (#rrggbb | #rrggbbaa), or null if invalid.
 */
export const colorToHex = (value: string, opt: Options = {}): string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const options = {
    ...opt
  };
  const resolved = preProcess(value, options);
  if (resolved === null) {
    return null;
  }
  const val = resolved.toLowerCase();
  const cacheKey = createCacheKey(
    { namespace: NAMESPACE, name: 'colorToHex', value: val },
    options
  );
  const cached = getCache(cacheKey);
  if (cached !== false) {
    return cached.item as string | null;
  }
  const hex = resolveColor(val, {
    ...options,
    nullable: true,
    format: options.alpha ? 'hexAlpha' : 'hex'
  });
  if (!isString(hex)) {
    setCache(cacheKey, null);
    return null;
  }
  setCache(cacheKey, hex);
  return hex;
};

/**
 * Converts a CSS color value to HSL color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [h, s, l, alpha].
 */
export const colorToHsl = createColorConverter(
  'colorToHsl',
  'hsl',
  convertColorToHsl
);

/**
 * Converts a CSS color value to HWB color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [h, w, b, alpha].
 */
export const colorToHwb = createColorConverter(
  'colorToHwb',
  'hwb',
  convertColorToHwb
);

/**
 * Converts a CSS color value to CIELAB color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [l, a, b, alpha].
 */
export const colorToLab = createColorConverter(
  'colorToLab',
  'lab',
  convertColorToLab
);

/**
 * Converts a CSS color value to CIELCH color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [l, c, h, alpha].
 */
export const colorToLch = createColorConverter(
  'colorToLch',
  'lch',
  convertColorToLch
);

/**
 * Converts a CSS color value to Oklab color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [l, a, b, alpha].
 */
export const colorToOklab = createColorConverter(
  'colorToOklab',
  'oklab',
  convertColorToOklab
);

/**
 * Converts a CSS color value to Oklch color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [l, c, h, alpha].
 */
export const colorToOklch = createColorConverter(
  'colorToOklch',
  'oklch',
  convertColorToOklch
);

/**
 * Converts a CSS color value to sRGB color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [r, g, b, alpha].
 */
export const colorToRgb = createColorConverter(
  'colorToRgb',
  'rgb',
  convertColorToRgb
);

/**
 * Converts a CSS color value to XYZ color channels (D65).
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [x, y, z, alpha].
 */
export const colorToXyz = (value: string, opt: Options = {}): ColorChannels => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const options = {
    ...opt
  };
  const resolved = preProcess(value, options);
  if (resolved === null) {
    return [0, 0, 0, 0];
  }
  const val = resolved.toLowerCase();
  const cacheKey = createCacheKey(
    { namespace: NAMESPACE, name: 'colorToXyz', value: val },
    options
  );
  const cached = getCache(cacheKey);
  if (cached !== false) {
    return [...(cached.item as ColorChannels)] as ColorChannels;
  }
  let parsed;
  if (val.startsWith('color(')) {
    parsed = parseColorFunc(val, options);
  } else {
    parsed = parseColorValue(val, options);
  }
  const [, ...xyz] = parsed as ComputedColorChannels;
  setCache(cacheKey, xyz);
  return [...xyz] as ColorChannels;
};

/**
 * Converts a CSS color value to XYZ-D50 color channels.
 * @param value - The CSS color value to convert.
 * @param [opt] - The conversion options.
 * @returns The color channels array: [x, y, z, alpha].
 */
export const colorToXyzD50 = (
  value: string,
  opt: Options = {}
): ColorChannels => {
  const options = {
    ...opt,
    d50: true
  };
  return colorToXyz(value, options);
};

/* Export convert */
export const convert = {
  colorToHex,
  colorToHsl,
  colorToHwb,
  colorToLab,
  colorToLch,
  colorToOklab,
  colorToOklch,
  colorToRgb,
  colorToXyz,
  colorToXyzD50,
  numberToHex
};
