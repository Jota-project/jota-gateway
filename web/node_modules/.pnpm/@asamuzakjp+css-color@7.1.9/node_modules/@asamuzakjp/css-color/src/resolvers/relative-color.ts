/**
 * CSS relative color utilities.
 */

import { SyntaxFlag, color as colorParser } from '@csstools/css-color-parser';
import {
  ComponentValue,
  parseComponentValue
} from '@csstools/css-parser-algorithms';
import { CSSToken, TokenType, tokenize } from '@csstools/css-tokenizer';
import { convertColorToRgb } from '../converters/convert-color';
import {
  ColorChannels,
  MatchedRegExp,
  NumberOrStringColorChannels,
  Options
} from '../typedef';
import { createCacheKey, getCache, setCache } from '../utils/cache';
import { isString, isStringOrNumber } from '../utils/common';
import { resolveDimension, serializeCalc } from '../utils/css-calc-var';
import { roundToPrecision, splitValue } from '../utils/util';

/* Constants */
import {
  COLOR_FUNC,
  DEC,
  FN_LIGHT_DARK,
  HEX,
  MAX_PCT,
  MAX_RGB,
  NONE,
  OCT,
  SYN_COLOR_TYPE,
  SYN_MIX,
  VAL_SPEC
} from '../utils/constant';
import { NAMED_COLORS } from './named-color';
const NAMESPACE = 'relative-color';
const {
  CloseParen: PAREN_CLOSE,
  Comment: COMMENT,
  Delim: DELIM,
  Dimension: DIM,
  EOF,
  Function: FUNC,
  Ident: IDENT,
  Number: NUM,
  OpenParen: PAREN_OPEN,
  Percentage: PCT,
  Whitespace: W_SPACE
} = TokenType;
const { HasNoneKeywords: KEY_NONE } = SyntaxFlag;
const COLOR_CHANNELS = new Map([
  ['color', ['r', 'g', 'b', 'alpha']],
  ['hsl', ['h', 's', 'l', 'alpha']],
  ['hsla', ['h', 's', 'l', 'alpha']],
  ['hwb', ['h', 'w', 'b', 'alpha']],
  ['lab', ['l', 'a', 'b', 'alpha']],
  ['lch', ['l', 'c', 'h', 'alpha']],
  ['oklab', ['l', 'a', 'b', 'alpha']],
  ['oklch', ['l', 'c', 'h', 'alpha']],
  ['rgb', ['r', 'g', 'b', 'alpha']],
  ['rgba', ['r', 'g', 'b', 'alpha']]
]);
const CIE_COLOR_SPACES = new Set(['lab', 'lch', 'oklab', 'oklch']);
const HSL_COLOR_SPACES = new Set(['hsl', 'hsla', 'hwb']);
const COLOR_FUNCTIONS = new Set([
  'color(',
  'hsl(',
  'hsla(',
  'hwb(',
  'lab(',
  'lch(',
  'oklab(',
  'oklch(',
  'rgb(',
  'rgba('
]);
const MATH_FUNCTIONS = new Set([
  'abs(',
  'acos(',
  'asin(',
  'atan(',
  'atan2(',
  'clamp(',
  'cos(',
  'exp(',
  'hypot(',
  'log(',
  'max(',
  'min(',
  'mod(',
  'pow(',
  'rem(',
  'round(',
  'sign(',
  'sin(',
  'sqrt(',
  'tan('
]);
const CALC_SUM_FUNCTIONS = new Set(['abs(', 'cos(', 'sign(', 'sin(', 'tan(']);

/* Regular expressions */
const REG_COLOR_CAPT = new RegExp(
  `^(?:${COLOR_FUNC})\\(\\s*from\\s+(${SYN_COLOR_TYPE}|${SYN_MIX})\\s+`,
  'i'
);
const REG_FN_REL_CAPT = new RegExp(`^(${COLOR_FUNC})\\(\\s*from\\s+`, 'i');
const REG_FN_REL_START = new RegExp(`^(?:${COLOR_FUNC})\\(\\s*from\\s+`, 'i');

/**
 * Serializes channel values into an array of numbers or strings.
 * @param channels - The color channels array.
 * @param format - The output format option.
 * @returns The resolved channel values.
 */
export const serializeChannels = (
  channels: [
    (number | string)[],
    (number | string)[],
    (number | string)[],
    (number | string)[]
  ],
  format: string
): NumberOrStringColorChannels => {
  const channelValues = [];
  for (const channel of channels) {
    if (channel.length === 1) {
      const [resolvedValue] = channel;
      if (isStringOrNumber(resolvedValue)) {
        channelValues.push(resolvedValue);
      }
    } else if (channel.length) {
      const resolvedValue = serializeCalc(channel.join(''), {
        format
      });
      channelValues.push(resolvedValue);
    }
  }
  return channelValues as NumberOrStringColorChannels;
};

/**
 * Gets the last non-whitespace value from a channel array.
 * @param channel - The channel array.
 * @returns The last non-whitespace value, or undefined if none exists.
 */
export const getLastNonWhitespaceValue = (
  channel: (number | string)[]
): string | number | undefined => {
  for (let i = channel.length - 1; i >= 0; i--) {
    if (channel[i] !== ' ') {
      return channel[i];
    }
  }
  return undefined;
};

/**
 * Resolves relative color channels from CSS tokens.
 * @param tokens - The CSS tokens to process.
 * @param [opt] - The conversion options.
 * @returns The resolved color channels, or null if invalid.
 */
export const resolveColorChannels = (
  tokens: CSSToken[],
  opt: Options = {}
): NumberOrStringColorChannels | null => {
  if (!Array.isArray(tokens)) {
    throw new TypeError(`${tokens} is not an array.`);
  }
  const { colorSpace = '', format = '' } = opt;
  const colorChannel = COLOR_CHANNELS.get(colorSpace);
  // Invalid color channel
  if (!colorChannel) {
    return null;
  }
  const mathFunc = new Set();
  const channels: [
    (number | string)[],
    (number | string)[],
    (number | string)[],
    (number | string)[]
  ] = [[], [], [], []];
  let i = 0;
  let nest = 0;
  let func = '';
  let precededPct = false;
  for (const token of tokens) {
    if (!Array.isArray(token)) {
      throw new TypeError(`${token} is not an array.`);
    }
    const [type, value, , , detail] = token as [
      TokenType,
      string,
      number,
      number,
      { value: string | number } | undefined
    ];
    const channel = channels[i];
    if (!Array.isArray(channel)) {
      continue;
    }
    switch (type) {
      case DELIM: {
        if (func) {
          if (
            (value === '+' || value === '-') &&
            precededPct &&
            !CALC_SUM_FUNCTIONS.has(func)
          ) {
            return null;
          }
          precededPct = false;
          channel.push(value);
        }
        break;
      }
      case DIM: {
        if (!func || !CALC_SUM_FUNCTIONS.has(func)) {
          return null;
        }
        const resolvedValue = resolveDimension(token, opt);
        if (isString(resolvedValue)) {
          channel.push(resolvedValue);
        } else {
          channel.push(value);
        }
        break;
      }
      case FUNC: {
        channel.push(value);
        func = value;
        nest++;
        if (MATH_FUNCTIONS.has(value)) {
          mathFunc.add(nest);
        }
        break;
      }
      case IDENT: {
        // Invalid channel key
        if (!colorChannel.includes(value)) {
          return null;
        }
        channel.push(value);
        if (!func) {
          i++;
        }
        break;
      }
      case NUM: {
        channel.push(Number(detail?.value));
        if (!func) {
          i++;
        }
        break;
      }
      case PAREN_OPEN: {
        channel.push(value);
        nest++;
        break;
      }
      case PAREN_CLOSE: {
        if (func) {
          const lastValue = channel.at(-1);
          if (lastValue === ' ') {
            channel[channel.length - 1] = value;
          } else {
            channel.push(value);
          }
          if (mathFunc.has(nest)) {
            mathFunc.delete(nest);
          }
          nest--;
          if (nest === 0) {
            func = '';
            i++;
          }
        }
        break;
      }
      case PCT: {
        if (!func) {
          return null;
        }
        if (!CALC_SUM_FUNCTIONS.has(func)) {
          const lastValue = getLastNonWhitespaceValue(channel);
          if (lastValue === '+' || lastValue === '-') {
            return null;
          }
          if (lastValue === '*' || lastValue === '/') {
            precededPct = false;
          } else {
            precededPct = true;
          }
        }
        channel.push(Number(detail?.value) / MAX_PCT);
        break;
      }
      case W_SPACE: {
        if (channel.length && func) {
          const lastValue = channel.at(-1);
          if (typeof lastValue === 'number') {
            channel.push(value);
          } else if (
            isString(lastValue) &&
            !lastValue.endsWith('(') &&
            lastValue !== ' '
          ) {
            channel.push(value);
          }
        }
        break;
      }
      default: {
        if (type !== COMMENT && type !== EOF && func) {
          channel.push(value);
        }
      }
    }
  }
  return serializeChannels(channels, format);
};

/**
 * Resolves the origin color when the light-dark() function is used.
 * @param value - The CSS color value string.
 * @param colorSpace - The color space name.
 * @param options - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color value, or null if resolution fails.
 */
export const resolveLightDarkOriginColor = (
  value: string,
  colorSpace: string,
  options: Options,
  resolver: (v: string, o?: Options) => string | null
): string | null => {
  const { colorScheme, format } = options;
  const colorParts = value
    .replace(new RegExp(`^${colorSpace}\\(`), '')
    .replace(/\)$/, '');
  const matchedColorParts = splitValue(colorParts);
  const [, originColor] = matchedColorParts as MatchedRegExp;
  const specifiedOriginColor = resolver(originColor, {
    colorScheme,
    format: VAL_SPEC
  }) as string;
  if (!specifiedOriginColor) {
    return null;
  }
  if (format === VAL_SPEC) {
    return value.replace(originColor, specifiedOriginColor);
  }
  const resolvedOriginColor = resolver(specifiedOriginColor, options);
  if (!isString(resolvedOriginColor)) {
    return null;
  }
  return value.replace(originColor, resolvedOriginColor);
};

/**
 * Resolves nested relative color origins and channels.
 * @param value - The CSS color value string.
 * @param options - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color value, or null if resolution fails.
 */
export const resolveNestedRelativeColor = (
  value: string,
  options: Options,
  resolver: (v: string, o?: Options) => string | null
): string | null => {
  const [, colorSpace] = value.match(REG_FN_REL_CAPT) as RegExpMatchArray;
  const opts = {
    ...options,
    colorSpace
  };
  const [, restValue] = value.split(REG_FN_REL_START) as MatchedRegExp;
  const tokens = tokenize({ css: restValue });
  const originColor: string[] = [];
  let nest = 0;
  let tokenIndex = 0;
  for (const [type, tokenValue] of tokens) {
    tokenIndex++;
    switch (type) {
      case FUNC:
      case PAREN_OPEN: {
        originColor.push(tokenValue);
        nest++;
        break;
      }
      case PAREN_CLOSE: {
        const lastValue = originColor.at(-1);
        if (lastValue === ' ') {
          originColor[originColor.length - 1] = tokenValue;
        } else {
          originColor.push(tokenValue);
        }
        nest--;
        break;
      }
      case W_SPACE: {
        const lastValue = originColor.at(-1);
        if (
          isString(lastValue) &&
          !lastValue.endsWith('(') &&
          lastValue !== ' '
        ) {
          originColor.push(tokenValue);
        }
        break;
      }
      default: {
        if (type !== COMMENT && type !== EOF) {
          originColor.push(tokenValue);
        }
      }
    }
    if (nest === 0) {
      break;
    }
  }
  const resolvedOriginColor = resolveRelativeColor(
    originColor.join('').trim(),
    opts,
    resolver
  );
  if (resolvedOriginColor === null) {
    return null;
  }
  const channelValues = resolveColorChannels(tokens.slice(tokenIndex), opts);
  if (channelValues === null) {
    return null;
  }
  const [v1, v2, v3, v4] = channelValues;
  let channelValue = '';
  if (isStringOrNumber(v4)) {
    channelValue = ` ${v1} ${v2} ${v3} / ${v4})`;
  } else {
    channelValue = ` ${channelValues.join(' ')})`;
  }
  return value.replace(restValue, `${resolvedOriginColor}${channelValue}`);
};

/**
 * Extracts the origin color from a relative color expression.
 * @param value - The CSS color value string.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The extracted origin color value, or null if invalid.
 */
export const extractOriginColor = (
  value: string,
  opt: Options = {},
  resolver: (v: string, o?: Options) => string | null = () => null
): string | null => {
  if (!isString(value)) {
    return null;
  }
  value = value.trim();
  if (!value) {
    return null;
  }
  if (!REG_FN_REL_START.test(value)) {
    return value;
  }
  const { currentColor, format } = opt;
  const cacheKey: string = createCacheKey(
    {
      namespace: NAMESPACE,
      name: 'extractOriginColor',
      value
    },
    opt
  );
  const cachedResult = getCache(cacheKey);
  if (cachedResult !== false) {
    return cachedResult.item as string | null;
  }
  if (/currentcolor/i.test(value)) {
    if (!currentColor) {
      setCache(cacheKey, null);
      return null;
    }
    value = value.replace(/currentcolor/gi, currentColor);
  }
  const matchRelFunc = value.match(REG_FN_REL_CAPT);
  const [, colorSpace] = matchRelFunc as MatchedRegExp;
  const options = {
    ...opt,
    colorSpace
  };
  if (value.includes(FN_LIGHT_DARK)) {
    const resolvedLightDark = resolveLightDarkOriginColor(
      value,
      colorSpace,
      options,
      resolver
    );
    if (resolvedLightDark === null) {
      setCache(cacheKey, null);
      return null;
    }
    value = resolvedLightDark;
  }
  if (REG_COLOR_CAPT.test(value)) {
    const [, originColor] = value.match(REG_COLOR_CAPT) as MatchedRegExp;
    const [, restValue] = value.split(originColor) as MatchedRegExp;
    if (/^[a-z]+$/.test(originColor)) {
      if (
        !/^transparent$/.test(originColor) &&
        !Object.hasOwn(NAMED_COLORS, originColor)
      ) {
        setCache(cacheKey, null);
        return null;
      }
    } else if (format === VAL_SPEC) {
      const resolvedOriginColor = resolver(originColor, options) as string;
      value = value.replace(originColor, resolvedOriginColor);
    }
    if (format === VAL_SPEC) {
      const tokens = tokenize({ css: restValue });
      const channelValues = resolveColorChannels(tokens, options);
      if (channelValues === null) {
        setCache(cacheKey, null);
        return null;
      }
      const [v1, v2, v3, v4] = channelValues;
      let channelValue = '';
      if (isStringOrNumber(v4)) {
        channelValue = ` ${v1} ${v2} ${v3} / ${v4})`;
      } else {
        channelValue = ` ${channelValues.join(' ')})`;
      }
      if (restValue !== channelValue) {
        value = value.replace(restValue, channelValue);
      }
    }
  } else {
    const resolvedNested = resolveNestedRelativeColor(value, options, resolver);
    if (resolvedNested === null) {
      setCache(cacheKey, null);
      return null;
    }
    value = resolvedNested;
  }
  setCache(cacheKey, value);
  return value;
};

/**
 * Checks whether the CSS tokens contain a var() function.
 * @param tokens - The CSS tokens to check.
 * @returns True if a var() function exists, otherwise false.
 */
export const hasCssVariables = (tokens: CSSToken[]): boolean => {
  for (const token of tokens) {
    const [type, tokenValue] = token;
    if (type === FUNC && tokenValue === 'var(') {
      return true;
    }
  }
  return false;
};

/**
 * Checks whether the CSS tokens contain relative color syntax.
 * @param tokens - The CSS tokens to check.
 * @returns True if relative color syntax exists, otherwise false.
 */
export const hasRelativeColorSyntax = (tokens: CSSToken[]): boolean => {
  for (let i = 0; i < tokens.length - 1; i++) {
    const [type, tokenValue] = tokens[i] as [TokenType, string];
    if (type === FUNC && COLOR_FUNCTIONS.has(tokenValue)) {
      for (let j = i + 1; j < tokens.length; j++) {
        const [nextType, nextValue] = tokens[j] as [TokenType, string];
        if (nextType === W_SPACE || nextType === COMMENT) {
          continue;
        }
        if (nextType === IDENT && nextValue === 'from') {
          return true;
        }
        break;
      }
    }
  }
  return false;
};

/**
 * Formats parsed color components into a standard CSS color string.
 * @param parsedComponents - The parsed color components from the color parser.
 * @returns The formatted CSS color string, or null if invalid.
 */
export const formatResolvedColor = (
  parsedComponents: ReturnType<typeof colorParser>
): string | null => {
  if (!parsedComponents) {
    return null;
  }
  const {
    alpha: alphaComponent,
    channels: channelsComponent,
    colorNotation,
    syntaxFlags
  } = parsedComponents;
  let alpha: number | string;
  if (Number.isNaN(Number(alphaComponent))) {
    if (syntaxFlags instanceof Set && syntaxFlags.has(KEY_NONE)) {
      alpha = NONE;
    } else {
      alpha = 0;
    }
  } else {
    alpha = roundToPrecision(Number(alphaComponent), OCT);
  }
  let v1: number | string;
  let v2: number | string;
  let v3: number | string;
  [v1, v2, v3] = channelsComponent;
  let resolvedValue;
  if (CIE_COLOR_SPACES.has(colorNotation)) {
    const hasNone = syntaxFlags instanceof Set && syntaxFlags.has(KEY_NONE);
    if (Number.isNaN(v1)) {
      if (hasNone) {
        v1 = NONE;
      } else {
        v1 = 0;
      }
    } else {
      v1 = roundToPrecision(v1, HEX);
    }
    if (Number.isNaN(v2)) {
      if (hasNone) {
        v2 = NONE;
      } else {
        v2 = 0;
      }
    } else {
      v2 = roundToPrecision(v2, HEX);
    }
    if (Number.isNaN(v3)) {
      if (hasNone) {
        v3 = NONE;
      } else {
        v3 = 0;
      }
    } else {
      v3 = roundToPrecision(v3, HEX);
    }
    if (alpha === 1) {
      resolvedValue = `${colorNotation}(${v1} ${v2} ${v3})`;
    } else {
      resolvedValue = `${colorNotation}(${v1} ${v2} ${v3} / ${alpha})`;
    }
  } else if (HSL_COLOR_SPACES.has(colorNotation)) {
    if (Number.isNaN(v1)) {
      v1 = 0;
    }
    if (Number.isNaN(v2)) {
      v2 = 0;
    }
    if (Number.isNaN(v3)) {
      v3 = 0;
    }
    let [r, g, b] = convertColorToRgb(
      `${colorNotation}(${v1} ${v2} ${v3} / ${alpha})`
    ) as ColorChannels;
    r = roundToPrecision(r / MAX_RGB, DEC);
    g = roundToPrecision(g / MAX_RGB, DEC);
    b = roundToPrecision(b / MAX_RGB, DEC);
    if (alpha === 1) {
      resolvedValue = `color(srgb ${r} ${g} ${b})`;
    } else {
      resolvedValue = `color(srgb ${r} ${g} ${b} / ${alpha})`;
    }
  } else {
    const cs = colorNotation === 'rgb' ? 'srgb' : colorNotation;
    const hasNone = syntaxFlags instanceof Set && syntaxFlags.has(KEY_NONE);
    if (Number.isNaN(v1)) {
      if (hasNone) {
        v1 = NONE;
      } else {
        v1 = 0;
      }
    } else {
      v1 = roundToPrecision(v1, DEC);
    }
    if (Number.isNaN(v2)) {
      if (hasNone) {
        v2 = NONE;
      } else {
        v2 = 0;
      }
    } else {
      v2 = roundToPrecision(v2, DEC);
    }
    if (Number.isNaN(v3)) {
      if (hasNone) {
        v3 = NONE;
      } else {
        v3 = 0;
      }
    } else {
      v3 = roundToPrecision(v3, DEC);
    }
    if (alpha === 1) {
      resolvedValue = `color(${cs} ${v1} ${v2} ${v3})`;
    } else {
      resolvedValue = `color(${cs} ${v1} ${v2} ${v3} / ${alpha})`;
    }
  }
  return resolvedValue;
};

/**
 * Resolves a CSS relative color value.
 * @param value - The CSS relative color string to resolve.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color string, or null if resolution fails.
 */
export function resolveRelativeColor(
  value: string,
  opt: Options = {},
  resolver: (v: string, o?: Options) => string | null = () => null
): string | null {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const options = {
    ...opt
  };
  const { format = '' } = options;
  const checkTokens = tokenize({ css: value });
  if (hasCssVariables(checkTokens)) {
    if (format !== VAL_SPEC) {
      throw new SyntaxError(`Unexpected token var( found.`);
    }
    return value;
  }
  if (!hasRelativeColorSyntax(checkTokens)) {
    return value;
  }
  const cacheKey: string = createCacheKey(
    {
      namespace: NAMESPACE,
      name: 'resolveRelativeColor',
      value
    },
    options
  );
  const cachedResult = getCache(cacheKey);
  if (cachedResult !== false) {
    return cachedResult.item as string | null;
  }
  const originColor = extractOriginColor(value, options, resolver);
  if (originColor === null) {
    setCache(cacheKey, null);
    return null;
  }
  const originColorValue = originColor;
  if (format === VAL_SPEC) {
    if (originColorValue.startsWith('rgba(')) {
      return originColorValue.replace('rgba(', 'rgb(');
    }
    if (originColorValue.startsWith('hsla(')) {
      return originColorValue.replace('hsla(', 'hsl(');
    }
    return originColorValue;
  }
  const tokens = tokenize({ css: originColorValue });
  const components = parseComponentValue(tokens) as ComponentValue;
  const parsedComponents = colorParser(components);
  const resolvedValue = formatResolvedColor(parsedComponents);
  if (resolvedValue === null) {
    setCache(cacheKey, null);
    return null;
  }
  setCache(cacheKey, resolvedValue);
  return resolvedValue;
}
