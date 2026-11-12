/**
 * Utility functions.
 */

import { ColorData, color as colorParser } from '@csstools/css-color-parser';
import {
  ComponentValue,
  isCommentNode,
  isWhitespaceNode,
  parseComponentValue,
  parseListOfComponentValues
} from '@csstools/css-parser-algorithms';
import { TokenType, tokenize } from '@csstools/css-tokenizer';
import {
  HueArc,
  MatchedRegExp,
  Options,
  SpecifiedColorChannels
} from '../typedef';
import { createCacheKey, getCache, setCache } from './cache';
import { isString } from './common';

/* Constants */
import {
  ANGLE,
  DEC,
  DEG,
  DEG_HALF,
  DUO,
  HEX,
  MAX_LENGTH,
  MAX_LENGTH_EXTEND,
  MAX_PCT,
  MAX_RGB,
  NONE,
  NUM,
  PPTH,
  TRIA,
  VAL_MIX,
  VAL_SPEC
} from './constant';
const GRAD = DEG / 400;
const RAD = DEG / (Math.PI * DUO);
const NAMESPACE = 'util';
const {
  CloseParen: PAREN_CLOSE,
  Comma: COMMA,
  Comment: COMMENT,
  Delim: DELIM,
  EOF,
  Function: FUNC,
  OpenParen: PAREN_OPEN,
  Whitespace: W_SPACE
} = TokenType;

/* Regular expressions */
const REG_ANGLE_TO_DEG = new RegExp(`^(${NUM})(${ANGLE})?$`);
const REG_COMMA = /^,$/;
const REG_DASHED_IDENT = /--[\w-]+/g;
const REG_SLASH = /^\/$/;
const REG_WHITESPACE = /^\s+$/;

/* Absolute font size to pixel ratio */
const absoluteFontSize = new Map([
  ['xx-small', 9 / 16],
  ['x-small', 5 / 8],
  ['small', 13 / 16],
  ['medium', 1],
  ['large', 9 / 8],
  ['x-large', 3 / 2],
  ['xx-large', 2],
  ['xxx-large', 3]
]);

/* Relative font size to pixel ratio */
const relativeFontSize = new Map([
  ['smaller', 1 / 1.2],
  ['larger', 1.2]
]);

/* Absolute length to pixel ratio */
const absoluteLength = new Map([
  ['cm', 96 / 2.54],
  ['mm', 96 / 25.4],
  ['q', 96 / 101.6],
  ['in', 96],
  ['pc', 16],
  ['pt', 96 / 72],
  ['px', 1]
]);

/* Relative length to pixel ratio */
const relativeLength = new Map([
  ['rcap', 1],
  ['rch', 0.5],
  ['rem', 1],
  ['rex', 0.5],
  ['ric', 1],
  ['rlh', 1.2]
]);

/**
 * Gets the maximum length allowed based on options and extension flag.
 * @param [opt] - Configuration options
 * @param [extend] - Whether to extend the maximum length limit
 * @returns The maximum length value
 */
export const getMaxLength = (
  opt: Options = {},
  extend: boolean = false
): number => {
  const { maxLength } = opt;
  const max = extend ? MAX_LENGTH_EXTEND : MAX_LENGTH;
  if (
    Number.isInteger(maxLength) &&
    (maxLength as number) > 0 &&
    (maxLength as number) < max
  ) {
    return maxLength as number;
  }
  return max;
};

/**
 * Splits a CSS value string by a delimiter.
 * NOTE: Comments are stripped by default, but can be preserved if the delimiter is ',' or '/' and `preserveComment` is set to `true`.
 * @param value - The CSS value string to split
 * @param [opt] - Configuration options
 * @returns An array of split value strings
 */
export const splitValue = (value: string, opt: Options = {}): string[] => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const str = value.trim();
  const options = {
    ...opt
  };
  const maxLength = getMaxLength(options, true);
  if (!str || str.length > maxLength) {
    return [];
  }
  const { delimiter = ' ', preserveComment = false } = options;
  const cacheKey: string = createCacheKey(
    {
      namespace: NAMESPACE,
      name: 'splitValue',
      value: str
    },
    { delimiter, preserveComment }
  );
  const cachedResult = getCache<string[]>(cacheKey);
  if (cachedResult) {
    return [...cachedResult.item];
  }
  let regDelimiter;
  switch (delimiter) {
    case ',': {
      regDelimiter = REG_COMMA;
      break;
    }
    case '/': {
      regDelimiter = REG_SLASH;
      break;
    }
    default: {
      regDelimiter = REG_WHITESPACE;
    }
  }
  const tokens = tokenize({ css: str });
  let nest = 0;
  let currentStr = '';
  const res: string[] = [];
  for (const [type, val] of tokens) {
    switch (type) {
      case COMMA:
      case DELIM: {
        if (nest === 0 && regDelimiter.test(val)) {
          res.push(currentStr.trim());
          currentStr = '';
        } else {
          currentStr += val;
        }
        break;
      }
      case COMMENT: {
        if (preserveComment && (delimiter === ',' || delimiter === '/')) {
          currentStr += val;
        }
        break;
      }
      case FUNC:
      case PAREN_OPEN: {
        currentStr += val;
        nest++;
        break;
      }
      case PAREN_CLOSE: {
        currentStr += val;
        nest--;
        break;
      }
      case W_SPACE: {
        if (regDelimiter.test(val)) {
          if (nest === 0) {
            if (currentStr) {
              res.push(currentStr.trim());
              currentStr = '';
            }
          } else {
            currentStr += ' ';
          }
        } else if (!currentStr.endsWith(' ')) {
          currentStr += ' ';
        }
        break;
      }
      default: {
        if (type === EOF) {
          res.push(currentStr.trim());
          currentStr = '';
        } else {
          currentStr += val;
        }
      }
    }
  }
  setCache(cacheKey, res);
  return [...res];
};

/**
 * Extracts dashed-ident tokens from a CSS value string.
 * @param value - The CSS value string to inspect
 * @returns An array of unique dashed-ident tokens
 */
export const extractDashedIdent = (value: string): string[] => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const str = value.trim();
  if (!str) {
    return [];
  }
  const cacheKey: string = createCacheKey({
    namespace: NAMESPACE,
    name: 'extractDashedIdent',
    value: str
  });
  const cachedResult = getCache<string[]>(cacheKey);
  if (cachedResult) {
    return [...cachedResult.item];
  }
  const matches = str.match(REG_DASHED_IDENT);
  const res = matches ? [...new Set(matches)] : [];
  setCache(cacheKey, res);
  return [...res];
};

/**
 * Rounds a numeric value to a specified precision bit level.
 * @param value - The numeric value to round
 * @param bit - The precision bit configuration
 * @returns The rounded numeric value
 */
export const roundToPrecision = (value: number, bit: number = 0): number => {
  if (!Number.isFinite(value)) {
    throw new TypeError(`${value} is not a finite number.`);
  }
  if (!Number.isFinite(bit)) {
    throw new TypeError(`${bit} is not a finite number.`);
  }
  if (bit < 0 || bit > HEX) {
    throw new RangeError(`${bit} is not between 0 and ${HEX}.`);
  }
  if (bit === 0) {
    return Math.round(value);
  }
  const precision = bit === HEX ? 6 : bit < DEC ? 4 : 5;
  return parseFloat(value.toPrecision(precision));
};

/**
 * Interpolates between two hue values based on the specified arc direction.
 * @param hueA - The first hue value
 * @param hueB - The second hue value
 * @param arc - The arc interpolation method ('shorter' | 'longer' | 'increasing' | 'decreasing')
 * @returns A tuple containing the adjusted [hueA, hueB] values
 */
export const interpolateHue = (
  hueA: number,
  hueB: number,
  arc: HueArc | string = 'shorter'
): [number, number] => {
  if (!Number.isFinite(hueA)) {
    throw new TypeError(`${hueA} is not a finite number.`);
  }
  if (!Number.isFinite(hueB)) {
    throw new TypeError(`${hueB} is not a finite number.`);
  }
  let a = hueA;
  let b = hueB;
  switch (arc) {
    case 'decreasing': {
      if (b > a) {
        a += DEG;
      }
      break;
    }
    case 'increasing': {
      if (b < a) {
        b += DEG;
      }
      break;
    }
    case 'longer': {
      if (b > a && b < a + DEG_HALF) {
        a += DEG;
      } else if (b > a - DEG_HALF && b <= a) {
        b += DEG;
      }
      break;
    }
    case 'shorter':
    default: {
      if (b > a + DEG_HALF) {
        a += DEG;
      } else if (b < a - DEG_HALF) {
        b += DEG;
      }
    }
  }
  return [a, b];
};

/**
 * Resolves a length or font size to its equivalent value in pixels.
 * @param value - The numeric value or string identifier to resolve
 * @param unit - The unit of the length
 * @param [opt] - Configuration options
 * @returns The resolved pixel value, or NaN on failure
 */
export const resolveLengthInPixels = (
  value: number | string,
  unit: string | undefined,
  opt: Options = {}
): number => {
  const { dimension = {} } = opt;
  const { callback, em, rem, vh, vw } = dimension;
  if (isString(value)) {
    const str = value.toLowerCase().trim();
    const maxLength = getMaxLength(opt);
    if (str.length > maxLength) {
      return Number.NaN;
    }
    const ratio = absoluteFontSize.get(str);
    if (ratio !== undefined && typeof rem === 'number') {
      return ratio * rem;
    }
    const relRatio = relativeFontSize.get(str);
    if (relRatio !== undefined && typeof em === 'number') {
      return relRatio * em;
    }
    return Number.NaN;
  }
  if (Number.isFinite(value) && unit) {
    const u = unit.toLowerCase();
    if (Object.hasOwn(dimension, u)) {
      const dimVal = dimension[u];
      return typeof dimVal === 'number'
        ? (value as number) * dimVal
        : Number.NaN;
    }
    if (typeof callback === 'function') {
      const cbVal = callback(u);
      return typeof cbVal === 'number' ? (value as number) * cbVal : Number.NaN;
    }
    const absRatio = absoluteLength.get(u);
    if (absRatio !== undefined) {
      return (value as number) * absRatio;
    }
    const relRatio = relativeLength.get(u);
    if (relRatio !== undefined && typeof rem === 'number') {
      return (value as number) * relRatio * rem;
    }
    const rUnitRatio = relativeLength.get(`r${u}`);
    if (rUnitRatio !== undefined && typeof em === 'number') {
      return (value as number) * rUnitRatio * em;
    }
    switch (u) {
      case 'vb': {
        return typeof vh === 'number' ? (value as number) * vh : Number.NaN;
      }
      case 'vi': {
        return typeof vw === 'number' ? (value as number) * vw : Number.NaN;
      }
      case 'vmax': {
        return typeof vh === 'number' && typeof vw === 'number'
          ? (value as number) * Math.max(vh, vw)
          : Number.NaN;
      }
      case 'vmin': {
        return typeof vh === 'number' && typeof vw === 'number'
          ? (value as number) * Math.min(vh, vw)
          : Number.NaN;
      }
      default:
    }
  }
  // Unsupported or invalid value
  return Number.NaN;
};

/**
 * Converts an angle string to degrees (normalized between 0 and 360).
 * @param value - The angle string to convert
 * @returns The resolved degree value (0 to 360)
 */
export const angleToDeg = (value: string): number => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const str = value.trim();
  if (!REG_ANGLE_TO_DEG.test(str)) {
    throw new SyntaxError(`Invalid property value: ${str}`);
  }
  const [, angle, unit] = str.match(REG_ANGLE_TO_DEG) as MatchedRegExp;
  let deg = parseFloat(angle);
  switch (unit) {
    case 'grad': {
      deg *= GRAD;
      break;
    }
    case 'rad': {
      deg *= RAD;
      break;
    }
    case 'turn': {
      deg *= DEG;
      break;
    }
    default:
  }
  deg %= DEG;
  if (deg < 0) {
    deg += DEG;
  } else if (Object.is(deg, -0)) {
    deg = 0;
  }
  return deg;
};

/**
 * Converts a numeric value to a two-digit hexadecimal string.
 * @param value - The numeric value to convert
 * @returns The resulting hexadecimal string
 */
export const numberToHexString = (value: number): string => {
  if (!Number.isFinite(value)) {
    throw new TypeError(`${value} is not a number.`);
  }
  const rounded = Math.round(value);
  if (rounded < 0 || rounded > MAX_RGB) {
    throw new RangeError(`${rounded} is not between 0 and ${MAX_RGB}.`);
  }
  const hex = rounded.toString(HEX);
  if (hex.length === 1) {
    return `0${hex}`;
  }
  return hex;
};

/**
 * Parses an alpha value string into a normalized number between 0 and 1.
 * @param [value] - The alpha value string to parse
 * @returns The normalized alpha value (0 to 1)
 */
export const parseAlpha = (value: string = ''): number => {
  if (!isString(value)) {
    return 1;
  }
  const str = value.trim();
  if (!str) {
    return 1;
  }
  if (str === NONE) {
    return 0;
  }
  let alpha;
  if (str.endsWith('%')) {
    alpha = parseFloat(str) / MAX_PCT;
  } else {
    alpha = parseFloat(str);
  }
  if (!Number.isFinite(alpha)) {
    throw new TypeError(`${alpha} is not a finite number.`);
  }
  if (alpha < PPTH) {
    return 0;
  }
  if (alpha > 1) {
    return 1;
  }
  return parseFloat(alpha.toFixed(TRIA));
};

/**
 * Parses a hexadecimal alpha string into a normalized number between 0 and 1.
 * @param value - The hexadecimal alpha string to parse
 * @returns The normalized alpha value (0 to 1)
 */
export const parseHexAlpha = (value: string): number => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const str = value.trim();
  if (!str) {
    throw new SyntaxError('Invalid property value: (empty string)');
  }
  let alpha = parseInt(str, HEX);
  if (alpha <= 0) {
    return 0;
  }
  if (alpha >= MAX_RGB) {
    return 1;
  }
  const alphaMap = new Map();
  for (let i = 1; i < MAX_PCT; i++) {
    alphaMap.set(Math.round((i * MAX_RGB) / MAX_PCT), i);
  }
  if (alphaMap.has(alpha)) {
    alpha = alphaMap.get(alpha) / MAX_PCT;
  } else {
    alpha = Math.round(alpha / MAX_RGB / PPTH) * PPTH;
  }
  return parseFloat(alpha.toFixed(TRIA));
};

/**
 * Parses a CSS color string generically into color data.
 * @param value - The CSS color value string
 * @returns The parsed color data object, or null if invalid
 */
export const parseColorGeneric = (value: string): ColorData | null => {
  const tokens = tokenize({ css: value });
  const parsed = colorParser(parseComponentValue(tokens) as ComponentValue);
  if (!parsed) {
    return null;
  }
  return parsed;
};

/**
 * Caches an invalid color value based on the given format and nullability.
 * @param cacheKey - The cache key
 * @param format - The output format
 * @param nullable - Whether the value can be null
 * @returns The cached default/fallback value
 */
export const cacheInvalidColorValue = (
  cacheKey: string,
  format: string,
  nullable: boolean = false
): SpecifiedColorChannels | string | null => {
  if (format === VAL_SPEC) {
    const res = '';
    setCache(cacheKey, res);
    return res;
  }
  if (nullable) {
    setCache(cacheKey, null);
    return null;
  }
  const res: SpecifiedColorChannels = ['rgb', 0, 0, 0, 0];
  setCache(cacheKey, res);
  return res;
};

/**
 * Resolves an invalid color value according to the output format and nullability.
 * @param format - The output format
 * @param nullable - Whether the value can be null
 * @returns The resolved fallback value
 */
export const resolveInvalidColorValue = (
  format: string,
  nullable: boolean = false
): SpecifiedColorChannels | string | null => {
  switch (format) {
    case 'hsl':
    case 'hwb':
    case VAL_MIX: {
      return null;
    }
    case VAL_SPEC: {
      return '';
    }
    default: {
      if (nullable) {
        return null;
      }
      return ['rgb', 0, 0, 0, 0] as SpecifiedColorChannels;
    }
  }
};

/**
 * Parses a CSS string into an array of component parts.
 * @param css - The CSS string to parse
 * @returns An array of component part strings
 */
export const parseCalcComponents = (css: string): string[] => {
  const components = parseListOfComponentValues(tokenize({ css }));
  const parts: string[] = [];
  for (const component of components) {
    if (!isCommentNode(component) && !isWhitespaceNode(component)) {
      parts.push(component.toString().trim());
    }
  }
  return parts;
};
