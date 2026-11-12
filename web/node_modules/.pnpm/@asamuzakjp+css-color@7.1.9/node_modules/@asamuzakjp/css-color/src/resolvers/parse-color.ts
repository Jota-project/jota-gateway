/**
 * Color parsing and conversion utilities.
 */

import { SyntaxFlag } from '@csstools/css-color-parser';
import { convertHexToRgb, convertHexToXyz } from '../converters/convert-hex';
import {
  transformMatrix,
  transformRgbToLinearRgb,
  transformRgbToXyz
} from '../matrix/transform';
import {
  ComputedColorChannels,
  Options,
  SpecifiedColorChannels,
  TriColorChannels
} from '../typedef';
import { isString } from '../utils/common';
import {
  parseAlpha,
  parseColorGeneric,
  resolveInvalidColorValue,
  roundToPrecision
} from '../utils/util';

/* Constants */
import {
  D50,
  MATRIX_A98_TO_XYZ,
  MATRIX_D50_TO_D65,
  MATRIX_D65_TO_D50,
  MATRIX_LMS_TO_XYZ,
  MATRIX_L_RGB_TO_XYZ,
  MATRIX_OKLAB_TO_LMS,
  MATRIX_P3_TO_XYZ,
  MATRIX_PROPHOTO_TO_XYZ_D50,
  MATRIX_REC2020_TO_XYZ
} from '../matrix/matrix';
import {
  DEC,
  DEG,
  DEG_HALF,
  DOZ,
  DUO,
  HEX,
  LAB_A,
  LAB_B,
  LAB_EPSILON,
  LAB_KAPPA,
  LAB_L,
  MAX_PCT,
  MAX_RGB,
  NONE,
  POW_CUBE,
  POW_SQR,
  SYN_COLOR_TYPE,
  TRIA,
  VAL_COMP,
  VAL_MIX,
  VAL_SPEC
} from '../utils/constant';
import { NAMED_COLORS } from './named-color';

/* Regular expressions */
const REG_COLOR = new RegExp(`^(?:${SYN_COLOR_TYPE})$`);
const REG_CURRENT = /^currentColor$/i;
const REG_SPEC = /^(?:specifi|comput)edValue$/;

/**
 * Parses an rgb() or rgba() CSS color function value.
 * @param value - The RGB color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color array ['rgb', r, g, b, alpha], an empty string, or null.
 */
export const parseRgb = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.toLowerCase().trim();
  const { format = '', nullable = false } = opt;
  const parsed = parseColorGeneric(value);
  if (!parsed || parsed.colorNotation !== 'rgb') {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  const [v1, v2, v3] = parsed.channels;
  const alphaVal = parsed.alpha;
  const hasNone =
    parsed.syntaxFlags instanceof Set &&
    parsed.syntaxFlags.has(SyntaxFlag.HasNoneKeywords);
  const r = Number.isNaN(v1)
    ? 0
    : Math.min(Math.max(roundToPrecision(v1 * MAX_RGB, HEX), 0), MAX_RGB);
  const g = Number.isNaN(v2)
    ? 0
    : Math.min(Math.max(roundToPrecision(v2 * MAX_RGB, HEX), 0), MAX_RGB);
  const b = Number.isNaN(v3)
    ? 0
    : Math.min(Math.max(roundToPrecision(v3 * MAX_RGB, HEX), 0), MAX_RGB);
  let alpha: number;
  if (typeof alphaVal === 'number' && !Number.isNaN(alphaVal)) {
    alpha = parseAlpha(String(alphaVal));
  } else {
    alpha = 0;
  }
  const isAlphaNone = Number.isNaN(alphaVal) && hasNone;
  return ['rgb', r, g, b, format === VAL_MIX && isAlphaNone ? NONE : alpha];
};

/**
 * Parses an hsl() or hsla() CSS color function value.
 * @param value - The HSL color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color array ['rgb', r, g, b, alpha] (or formatted channels), an empty string, or null.
 */
export const parseHsl = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const { format = '', nullable = false } = opt;
  const parsed = parseColorGeneric(value);
  if (!parsed || parsed.colorNotation !== 'hsl') {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  const [v1, v2, v3] = parsed.channels;
  const alphaVal = parsed.alpha;
  const hasNone =
    parsed.syntaxFlags instanceof Set &&
    parsed.syntaxFlags.has(SyntaxFlag.HasNoneKeywords);
  let h = Number.isNaN(v1) ? 0 : v1;
  const s = Number.isNaN(v2) ? 0 : Math.min(Math.max(v2, 0), MAX_PCT);
  let l = Number.isNaN(v3) ? 0 : Math.min(Math.max(v3, 0), MAX_PCT);
  let alpha: number;
  if (typeof alphaVal === 'number' && !Number.isNaN(alphaVal)) {
    alpha = parseAlpha(String(alphaVal));
  } else {
    alpha = 0;
  }
  const isAlphaNone = Number.isNaN(alphaVal) && hasNone;
  if (format === 'hsl') {
    return [
      format,
      Number.isNaN(v1) ? NONE : h,
      Number.isNaN(v2) ? NONE : s,
      Number.isNaN(v3) ? NONE : l,
      isAlphaNone ? NONE : alpha
    ];
  }
  h = (h / DEG) * DOZ;
  l /= MAX_PCT;
  const sa = (s / MAX_PCT) * Math.min(l, 1 - l);
  const rk = h % DOZ;
  const gk = (8 + h) % DOZ;
  const bk = (4 + h) % DOZ;
  const r = l - sa * Math.max(-1, Math.min(rk - TRIA, TRIA ** POW_SQR - rk, 1));
  const g = l - sa * Math.max(-1, Math.min(gk - TRIA, TRIA ** POW_SQR - gk, 1));
  const b = l - sa * Math.max(-1, Math.min(bk - TRIA, TRIA ** POW_SQR - bk, 1));
  return [
    'rgb',
    Math.min(Math.max(roundToPrecision(r * MAX_RGB, HEX), 0), MAX_RGB),
    Math.min(Math.max(roundToPrecision(g * MAX_RGB, HEX), 0), MAX_RGB),
    Math.min(Math.max(roundToPrecision(b * MAX_RGB, HEX), 0), MAX_RGB),
    alpha
  ];
};

/**
 * Parses an hwb() CSS color function value.
 * @param value - The HWB color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color array ['rgb', r, g, b, alpha] (or formatted channels), an empty string, or null.
 */
export const parseHwb = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const { format = '', nullable = false } = opt;
  const parsed = parseColorGeneric(value);
  if (!parsed || parsed.colorNotation !== 'hwb') {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  const [v1, v2, v3] = parsed.channels;
  const alphaVal = parsed.alpha;
  const hasNone =
    parsed.syntaxFlags instanceof Set &&
    parsed.syntaxFlags.has(SyntaxFlag.HasNoneKeywords);
  const h = Number.isNaN(v1) ? 0 : v1;
  const wh = Number.isNaN(v2)
    ? 0
    : Math.min(Math.max(v2, 0), MAX_PCT) / MAX_PCT;
  const bk = Number.isNaN(v3)
    ? 0
    : Math.min(Math.max(v3, 0), MAX_PCT) / MAX_PCT;
  let alpha: number;
  if (typeof alphaVal === 'number' && !Number.isNaN(alphaVal)) {
    alpha = parseAlpha(String(alphaVal));
  } else {
    alpha = 0;
  }
  const isAlphaNone = Number.isNaN(alphaVal) && hasNone;
  if (format === 'hwb') {
    return [
      format,
      Number.isNaN(v1) ? NONE : h,
      Number.isNaN(v2) ? NONE : wh * MAX_PCT,
      Number.isNaN(v3) ? NONE : bk * MAX_PCT,
      isAlphaNone ? NONE : alpha
    ];
  }
  if (wh + bk >= 1) {
    const v = roundToPrecision((wh / (wh + bk)) * MAX_RGB, HEX);
    return ['rgb', v, v, v, alpha];
  }
  const factor = (1 - wh - bk) / MAX_RGB;
  let [, r, g, b] = parseHsl(`hsl(${h} 100 50)`) as ComputedColorChannels;
  r = roundToPrecision((r * factor + wh) * MAX_RGB, HEX);
  g = roundToPrecision((g * factor + wh) * MAX_RGB, HEX);
  b = roundToPrecision((b * factor + wh) * MAX_RGB, HEX);
  return [
    'rgb',
    Math.min(Math.max(r, 0), MAX_RGB),
    Math.min(Math.max(g, 0), MAX_RGB),
    Math.min(Math.max(b, 0), MAX_RGB),
    alpha
  ];
};

/**
 * Parses a lab() CSS color function value.
 * @param value - The lab color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-d50', x, y, z, alpha] or ['lab', l, a, b, alpha], an empty string, or null.
 */
export const parseLab = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const { format = '', nullable = false } = opt;
  const parsed = parseColorGeneric(value);
  if (!parsed || parsed.colorNotation !== 'lab') {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  const [v1, v2, v3] = parsed.channels;
  const alphaVal = parsed.alpha;
  const hasNone =
    parsed.syntaxFlags instanceof Set &&
    parsed.syntaxFlags.has(SyntaxFlag.HasNoneKeywords);
  const COND_POW = 8;
  const l = Number.isNaN(v1) ? 0 : Math.max(v1, 0);
  const a = Number.isNaN(v2) ? 0 : v2;
  const b = Number.isNaN(v3) ? 0 : v3;
  let alpha: number;
  if (typeof alphaVal === 'number' && !Number.isNaN(alphaVal)) {
    alpha = parseAlpha(String(alphaVal));
  } else {
    alpha = 0;
  }
  const isAlphaNone = Number.isNaN(alphaVal) && hasNone;
  if (REG_SPEC.test(format)) {
    return [
      'lab',
      Number.isNaN(v1) ? NONE : roundToPrecision(l, HEX),
      Number.isNaN(v2) ? NONE : roundToPrecision(a, HEX),
      Number.isNaN(v3) ? NONE : roundToPrecision(b, HEX),
      isAlphaNone ? NONE : alpha
    ];
  }
  const fl = (l + HEX) / LAB_L;
  const fa = a / LAB_A + fl;
  const fb = fl - b / LAB_B;
  const powFl = Math.pow(fl, POW_CUBE);
  const powFa = Math.pow(fa, POW_CUBE);
  const powFb = Math.pow(fb, POW_CUBE);
  const xyz = [
    powFa > LAB_EPSILON ? powFa : (fa * LAB_L - HEX) / LAB_KAPPA,
    l > COND_POW ? powFl : l / LAB_KAPPA,
    powFb > LAB_EPSILON ? powFb : (fb * LAB_L - HEX) / LAB_KAPPA
  ];
  const [x, y, z] = xyz.map(
    (val, i) => val * (D50[i] as number)
  ) as TriColorChannels;
  return [
    'xyz-d50',
    roundToPrecision(x, HEX),
    roundToPrecision(y, HEX),
    roundToPrecision(z, HEX),
    alpha
  ];
};

/**
 * Parses an lch() CSS color function value.
 * @param value - The lch color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-d50', x, y, z, alpha] or ['lch', l, c, h, alpha], an empty string, or null.
 */
export const parseLch = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const { format = '', nullable = false } = opt;
  const parsed = parseColorGeneric(value);
  if (!parsed || parsed.colorNotation !== 'lch') {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  const [v1, v2, v3] = parsed.channels;
  const alphaVal = parsed.alpha;
  const hasNone =
    parsed.syntaxFlags instanceof Set &&
    parsed.syntaxFlags.has(SyntaxFlag.HasNoneKeywords);
  const l = Number.isNaN(v1) ? 0 : Math.max(v1, 0);
  const c = Number.isNaN(v2) ? 0 : Math.max(v2, 0);
  const h = Number.isNaN(v3) ? 0 : v3;
  let alpha: number;
  if (typeof alphaVal === 'number' && !Number.isNaN(alphaVal)) {
    alpha = parseAlpha(String(alphaVal));
  } else {
    alpha = 0;
  }
  const isAlphaNone = Number.isNaN(alphaVal) && hasNone;
  if (REG_SPEC.test(format)) {
    return [
      'lch',
      Number.isNaN(v1) ? NONE : roundToPrecision(l, HEX),
      Number.isNaN(v2) ? NONE : roundToPrecision(c, HEX),
      Number.isNaN(v3) ? NONE : roundToPrecision(h, HEX),
      isAlphaNone ? NONE : alpha
    ];
  }
  const a = c * Math.cos((h * Math.PI) / DEG_HALF);
  const b = c * Math.sin((h * Math.PI) / DEG_HALF);
  const [, x, y, z] = parseLab(`lab(${l} ${a} ${b})`) as ComputedColorChannels;
  return [
    'xyz-d50',
    roundToPrecision(x, HEX),
    roundToPrecision(y, HEX),
    roundToPrecision(z, HEX),
    alpha
  ];
};

/**
 * Parses an oklab() CSS color function value.
 * @param value - The oklab color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-d65', x, y, z, alpha] or ['oklab', l, a, b, alpha], an empty string, or null.
 */
export const parseOklab = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const { format = '', nullable = false } = opt;
  const parsed = parseColorGeneric(value);
  if (!parsed || parsed.colorNotation !== 'oklab') {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  const [v1, v2, v3] = parsed.channels;
  const alphaVal = parsed.alpha;
  const hasNone =
    parsed.syntaxFlags instanceof Set &&
    parsed.syntaxFlags.has(SyntaxFlag.HasNoneKeywords);
  const l = Number.isNaN(v1) ? 0 : Math.max(v1, 0);
  const a = Number.isNaN(v2) ? 0 : v2;
  const b = Number.isNaN(v3) ? 0 : v3;
  let alpha: number;
  if (typeof alphaVal === 'number' && !Number.isNaN(alphaVal)) {
    alpha = parseAlpha(String(alphaVal));
  } else {
    alpha = 0;
  }
  const isAlphaNone = Number.isNaN(alphaVal) && hasNone;
  if (REG_SPEC.test(format)) {
    return [
      'oklab',
      Number.isNaN(v1) ? NONE : roundToPrecision(l, HEX),
      Number.isNaN(v2) ? NONE : roundToPrecision(a, HEX),
      Number.isNaN(v3) ? NONE : roundToPrecision(b, HEX),
      isAlphaNone ? NONE : alpha
    ];
  }
  const lms = transformMatrix(MATRIX_OKLAB_TO_LMS, [l, a, b]);
  const xyzLms = lms.map((c: number) =>
    Math.pow(c, POW_CUBE)
  ) as TriColorChannels;
  const [x, y, z] = transformMatrix(MATRIX_LMS_TO_XYZ, xyzLms, true);
  return [
    'xyz-d65',
    roundToPrecision(x, HEX),
    roundToPrecision(y, HEX),
    roundToPrecision(z, HEX),
    alpha as number
  ];
};

/**
 * Parses an oklch() CSS color function value.
 * @param value - The oklch color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-d65', x, y, z, alpha] or ['oklch', l, c, h, alpha], an empty string, or null.
 */
export const parseOklch = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const { format = '', nullable = false } = opt;
  const parsed = parseColorGeneric(value);
  if (!parsed || parsed.colorNotation !== 'oklch') {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  const [v1, v2, v3] = parsed.channels;
  const alphaVal = parsed.alpha;
  const hasNone =
    parsed.syntaxFlags instanceof Set &&
    parsed.syntaxFlags.has(SyntaxFlag.HasNoneKeywords);
  const l = Number.isNaN(v1) ? 0 : Math.max(v1, 0);
  const c = Number.isNaN(v2) ? 0 : Math.max(v2, 0);
  const h = Number.isNaN(v3) ? 0 : v3;
  let alpha: number;
  if (typeof alphaVal === 'number' && !Number.isNaN(alphaVal)) {
    alpha = parseAlpha(String(alphaVal));
  } else {
    alpha = 0;
  }
  const isAlphaNone = Number.isNaN(alphaVal) && hasNone;
  if (REG_SPEC.test(format)) {
    return [
      'oklch',
      Number.isNaN(v1) ? NONE : roundToPrecision(l, HEX),
      Number.isNaN(v2) ? NONE : roundToPrecision(c, HEX),
      Number.isNaN(v3) ? NONE : roundToPrecision(h, HEX),
      isAlphaNone ? NONE : alpha
    ];
  }
  const a = c * Math.cos((h * Math.PI) / DEG_HALF);
  const b = c * Math.sin((h * Math.PI) / DEG_HALF);
  const lms = transformMatrix(MATRIX_OKLAB_TO_LMS, [l, a, b]);
  const xyzLms = lms.map((cc: number) =>
    Math.pow(cc, POW_CUBE)
  ) as TriColorChannels;
  const [x, y, z] = transformMatrix(MATRIX_LMS_TO_XYZ, xyzLms, true);
  return [
    'xyz-d65',
    roundToPrecision(x, HEX),
    roundToPrecision(y, HEX),
    roundToPrecision(z, HEX),
    alpha
  ];
};

/**
 * Parses a generic color() CSS function value.
 * @param value - The color function value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color channels array: ['xyz-(d50|d65)', x, y, z, alpha] or [cs, r, g, b, alpha], an empty string, or null.
 */
export const parseColorFunc = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const { colorSpace = '', d50 = false, format = '', nullable = false } = opt;
  const parsed = parseColorGeneric(value);
  if (!parsed) {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  const cs = parsed.colorNotation;
  const [v1, v2, v3] = parsed.channels;
  const alphaVal = parsed.alpha;
  const hasNone =
    parsed.syntaxFlags instanceof Set &&
    parsed.syntaxFlags.has(SyntaxFlag.HasNoneKeywords);
  const r = Number.isNaN(v1) ? 0 : v1;
  const g = Number.isNaN(v2) ? 0 : v2;
  const b = Number.isNaN(v3) ? 0 : v3;
  let alpha: number;
  if (typeof alphaVal === 'number' && !Number.isNaN(alphaVal)) {
    alpha = parseAlpha(String(alphaVal));
  } else {
    alpha = 0;
  }
  const isAlphaNone = Number.isNaN(alphaVal) && hasNone;
  if (REG_SPEC.test(format) || (format === VAL_MIX && cs === colorSpace)) {
    return [
      cs,
      Number.isNaN(v1) ? NONE : roundToPrecision(r, HEX),
      Number.isNaN(v2) ? NONE : roundToPrecision(g, HEX),
      Number.isNaN(v3) ? NONE : roundToPrecision(b, HEX),
      isAlphaNone ? NONE : alpha
    ];
  }
  let x = 0;
  let y = 0;
  let z = 0;
  // srgb-linear
  if (cs === 'srgb-linear') {
    [x, y, z] = transformMatrix(MATRIX_L_RGB_TO_XYZ, [r, g, b]);
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  } else if (cs === 'display-p3') {
    // display-p3
    const linearRgb = transformRgbToLinearRgb([
      r * MAX_RGB,
      g * MAX_RGB,
      b * MAX_RGB
    ]);
    [x, y, z] = transformMatrix(MATRIX_P3_TO_XYZ, linearRgb);
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  } else if (cs === 'rec2020') {
    // rec2020
    const ALPHA = 1.09929682680944;
    const BETA = 0.018053968510807;
    const REC_COEF = 0.45;
    const rgb = [r, g, b].map(c => {
      let cl;
      if (c < BETA * REC_COEF * DEC) {
        cl = c / (REC_COEF * DEC);
      } else {
        cl = Math.pow((c + ALPHA - 1) / ALPHA, 1 / REC_COEF);
      }
      return cl;
    }) as TriColorChannels;
    [x, y, z] = transformMatrix(MATRIX_REC2020_TO_XYZ, rgb);
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  } else if (cs === 'a98-rgb') {
    // a98-rgb
    const POW_A98 = 563 / 256;
    const rgb = [r, g, b].map(c => {
      const cl = Math.pow(c, POW_A98);
      return cl;
    }) as TriColorChannels;
    [x, y, z] = transformMatrix(MATRIX_A98_TO_XYZ, rgb);
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  } else if (cs === 'prophoto-rgb') {
    // prophoto-rgb
    const POW_PROPHOTO = 1.8;
    const rgb = [r, g, b].map(c => {
      let cl;
      if (c > 1 / (HEX * DUO)) {
        cl = Math.pow(c, POW_PROPHOTO);
      } else {
        cl = c / HEX;
      }
      return cl;
    }) as TriColorChannels;
    [x, y, z] = transformMatrix(MATRIX_PROPHOTO_TO_XYZ_D50, rgb);
    if (!d50) {
      [x, y, z] = transformMatrix(MATRIX_D50_TO_D65, [x, y, z], true);
    }
  } else if (/^xyz(?:-d(?:50|65))?$/.test(cs)) {
    // xyz, xyz-d50, xyz-d65
    [x, y, z] = [r, g, b];
    if (cs === 'xyz-d50') {
      if (!d50) {
        [x, y, z] = transformMatrix(MATRIX_D50_TO_D65, [x, y, z]);
      }
    } else if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  } else {
    // srgb
    [x, y, z] = transformRgbToXyz([r * MAX_RGB, g * MAX_RGB, b * MAX_RGB]);
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  }
  return [
    d50 ? 'xyz-d50' : 'xyz-d65',
    roundToPrecision(x, HEX),
    roundToPrecision(y, HEX),
    roundToPrecision(z, HEX),
    format === VAL_MIX && isAlphaNone ? NONE : alpha
  ];
};

/**
 * Parses a CSS color value string of any supported format.
 * @param value - The CSS color value string to parse.
 * @param opt - The parsing options.
 * @returns The parsed color array/channels, an empty string, or null.
 */
export const parseColorValue = (
  value: string,
  opt: Options = {}
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.toLowerCase().trim();
  const { d50 = false, format = '', nullable = false } = opt;
  if (!REG_COLOR.test(value)) {
    const res = resolveInvalidColorValue(format, nullable);
    if (res === null) {
      return null;
    } else if (isString(res)) {
      return res as string;
    }
    return res as SpecifiedColorChannels;
  }
  let x = 0;
  let y = 0;
  let z = 0;
  let alpha = 0;
  // complement currentcolor as a missing color
  if (REG_CURRENT.test(value)) {
    if (format === VAL_COMP) {
      return ['rgb', 0, 0, 0, 0];
    }
    if (format === VAL_SPEC) {
      return value;
    }
  } else if (/^[a-z]+$/.test(value)) {
    // named-color
    if (Object.hasOwn(NAMED_COLORS, value)) {
      if (format === VAL_SPEC) {
        return value;
      }
      const [r, g, b] = NAMED_COLORS[
        value as keyof typeof NAMED_COLORS
      ] as TriColorChannels;
      alpha = 1;
      if (format === VAL_COMP) {
        return ['rgb', r, g, b, alpha];
      }
      [x, y, z] = transformRgbToXyz([r, g, b], true);
      if (d50) {
        [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
      }
    } else {
      switch (format) {
        case VAL_COMP: {
          if (nullable && value !== 'transparent') {
            return null;
          }
          return ['rgb', 0, 0, 0, 0];
        }
        case VAL_SPEC: {
          if (value === 'transparent') {
            return value;
          }
          return '';
        }
        case VAL_MIX: {
          if (value === 'transparent') {
            return ['rgb', 0, 0, 0, 0];
          }
          return null;
        }
        default:
      }
    }
  } else if (value[0] === '#') {
    // hex-color
    if (REG_SPEC.test(format)) {
      const rgb = convertHexToRgb(value);
      return ['rgb', ...rgb];
    }
    [x, y, z, alpha] = convertHexToXyz(value);
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  } else if (value.startsWith('lab')) {
    // lab()
    if (REG_SPEC.test(format)) {
      return parseLab(value, opt);
    }
    [, x, y, z, alpha] = parseLab(value) as ComputedColorChannels;
    if (!d50) {
      [x, y, z] = transformMatrix(MATRIX_D50_TO_D65, [x, y, z], true);
    }
  } else if (value.startsWith('lch')) {
    // lch()
    if (REG_SPEC.test(format)) {
      return parseLch(value, opt);
    }
    [, x, y, z, alpha] = parseLch(value) as ComputedColorChannels;
    if (!d50) {
      [x, y, z] = transformMatrix(MATRIX_D50_TO_D65, [x, y, z], true);
    }
  } else if (value.startsWith('oklab')) {
    // oklab()
    if (REG_SPEC.test(format)) {
      return parseOklab(value, opt);
    }
    [, x, y, z, alpha] = parseOklab(value) as ComputedColorChannels;
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  } else if (value.startsWith('oklch')) {
    // oklch()
    if (REG_SPEC.test(format)) {
      return parseOklch(value, opt);
    }
    [, x, y, z, alpha] = parseOklch(value) as ComputedColorChannels;
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  } else {
    let r, g, b;
    // hsl()
    if (value.startsWith('hsl')) {
      [, r, g, b, alpha] = parseHsl(value) as ComputedColorChannels;
    } else if (value.startsWith('hwb')) {
      // hwb()
      [, r, g, b, alpha] = parseHwb(value) as ComputedColorChannels;
    } else {
      // rgb()
      [, r, g, b, alpha] = parseRgb(value, opt) as ComputedColorChannels;
    }
    if (REG_SPEC.test(format)) {
      return ['rgb', Math.round(r), Math.round(g), Math.round(b), alpha];
    }
    [x, y, z] = transformRgbToXyz([r, g, b]);
    if (d50) {
      [x, y, z] = transformMatrix(MATRIX_D65_TO_D50, [x, y, z], true);
    }
  }
  return [
    d50 ? 'xyz-d50' : 'xyz-d65',
    roundToPrecision(x, HEX),
    roundToPrecision(y, HEX),
    roundToPrecision(z, HEX),
    alpha
  ];
};
