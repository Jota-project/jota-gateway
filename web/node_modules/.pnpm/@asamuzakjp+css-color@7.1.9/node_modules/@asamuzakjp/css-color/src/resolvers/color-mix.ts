/**
 * CSS color-mix() utilities.
 */

import {
  convertColorToHsl,
  convertColorToHwb,
  convertColorToLab,
  convertColorToLch,
  convertColorToLinearRgb,
  convertColorToOklab,
  convertColorToOklch,
  convertColorToRgb,
  convertColorToXyz
} from '../converters/convert-color';
import {
  ChannelNoneFlags,
  ColorAndPct,
  ColorAndRatio,
  ColorChannels,
  ColorFormat,
  ColorMixInnerColorItem,
  ColorMixInnerContent,
  ColorResolverFn,
  ColorSpace,
  ColorSpaceParseResult,
  ColorsAndMultipler,
  ComputedColorChannels,
  InterpolatedComponents,
  NormalizedPercentages,
  NumStrColorChannels,
  Options,
  SpecifiedColorChannels,
  TriColorChannels
} from '../typedef';
import {
  normalizeColorComponents,
  transformXyzD50ToRgb,
  transformXyzToRgb
} from '../matrix/transform';
import { parseColorFunc, parseColorValue } from '../resolvers/parse-color';
import { resolveColorValue } from '../resolvers/resolve-color';
import { createCacheKey, getCache, setCache } from '../utils/cache';
import { isString } from '../utils/common';
import { cssCalc } from '../utils/css-calc-var';
import {
  cacheInvalidColorValue,
  interpolateHue,
  roundToPrecision,
  splitValue
} from '../utils/util';

/* Constants */
import {
  DEG,
  FN_CALC,
  FN_COLOR,
  FN_LIGHT_DARK,
  FN_MIX,
  FN_VAR,
  HALF,
  HEX,
  MAX_PCT,
  MAX_RGB,
  NONE,
  VAL_COMP,
  VAL_MIX,
  VAL_SPEC
} from '../utils/constant';
const NAMESPACE = 'color-mix';

/**
 * Normalizes the percentages for color mixing.
 * @param pctA - The percentage string for the first color.
 * @param pctB - The percentage string for the second color.
 * @returns An object containing normalized percentages and a multiplier, or null if invalid.
 */
export const normalizePercentages = (
  pctA: string,
  pctB: string
): NormalizedPercentages | null => {
  let pA, pB, m;
  if (pctA && pctB) {
    const p1 = parseFloat(pctA) / MAX_PCT;
    const p2 = parseFloat(pctB) / MAX_PCT;
    if (
      Number.isNaN(p1) ||
      Number.isNaN(p2) ||
      p1 < 0 ||
      p1 > 1 ||
      p2 < 0 ||
      p2 > 1 ||
      (p1 === 0 && p2 === 0)
    ) {
      return null;
    }
    const factor = p1 + p2;
    pA = p1 / factor;
    pB = p2 / factor;
    m = factor < 1 ? factor : 1;
  } else {
    if (pctA) {
      const numA = parseFloat(pctA);
      if (Number.isNaN(numA) || numA < 0 || numA > MAX_PCT) {
        return null;
      }
      pA = numA / MAX_PCT;
      pB = (MAX_PCT - numA) / MAX_PCT;
    } else if (pctB) {
      const numB = parseFloat(pctB);
      if (Number.isNaN(numB) || numB < 0 || numB > MAX_PCT) {
        return null;
      }
      pB = numB / MAX_PCT;
      pA = (MAX_PCT - numB) / MAX_PCT;
    } else {
      pA = HALF;
      pB = HALF;
    }
    m = 1;
  }
  return { pA, pB, m };
};

/**
 * Performs alpha-weighted interpolation for color components.
 * @param compA - The color components for the first color.
 * @param compB - The color components for the second color.
 * @param alphaA - The alpha channel value for the first color.
 * @param alphaB - The alpha channel value for the second color.
 * @param pA - The normalized ratio for the first color.
 * @param pB - The normalized ratio for the second color.
 * @param invalidate - Indicates whether to invalidate values if alpha is zero.
 * @returns An object containing the interpolated components and the combined alpha value.
 */
export const interpolateComponents = (
  compA: TriColorChannels,
  compB: TriColorChannels,
  alphaA: number,
  alphaB: number,
  pA: number,
  pB: number,
  invalidate: boolean = false
): InterpolatedComponents => {
  const clampedAlphaA = Math.max(0, Math.min(1, alphaA));
  const clampedAlphaB = Math.max(0, Math.min(1, alphaB));
  const factorA = clampedAlphaA * pA;
  const factorB = clampedAlphaB * pB;
  const alpha = factorA + factorB;
  let comps: TriColorChannels;
  if (alpha === 0) {
    if (invalidate) {
      comps = [0, 0, 0];
    } else {
      comps = [
        compA[0] * pA + compB[0] * pB,
        compA[1] * pA + compB[1] * pB,
        compA[2] * pA + compB[2] * pB
      ];
    }
  } else {
    comps = [
      (compA[0] * factorA + compB[0] * factorB) / alpha,
      (compA[1] * factorA + compB[1] * factorB) / alpha,
      (compA[2] * factorA + compB[2] * factorB) / alpha
    ];
  }
  return {
    comps,
    alpha: Math.max(0, Math.min(1, alpha))
  };
};

/**
 * Formats a computed mixed color into a specified or computed channel array.
 * @param colorSpace - The target color space.
 * @param comps - The 3-channel color components.
 * @param nones - The boolean flags indicating if channels are marked as 'none'.
 * @param alpha - The calculated alpha channel value.
 * @param m - The multiplier factor.
 * @param format - The output format option.
 * @param rgbOverride - Optional pre-calculated RGB channels.
 * @returns The specified color channels array.
 */
export const formatMixedColor = (
  colorSpace: ColorSpace,
  comps: TriColorChannels,
  nones: ChannelNoneFlags,
  alpha: number,
  m: number,
  format: ColorFormat,
  rgbOverride?: TriColorChannels
): SpecifiedColorChannels => {
  const [c1None, c2None, c3None, alphaNone] = nones;
  const [c1, c2, c3] = comps;
  if (format === VAL_COMP) {
    return [
      colorSpace,
      c1None ? NONE : roundToPrecision(c1, HEX),
      c2None ? NONE : roundToPrecision(c2, HEX),
      c3None ? NONE : roundToPrecision(c3, HEX),
      alphaNone ? NONE : alpha * m
    ];
  }
  const [r, g, b] = rgbOverride ?? comps;
  return [
    'rgb',
    Math.round(r),
    Math.round(g),
    Math.round(b),
    parseFloat((alpha * m).toFixed(3))
  ];
};

/**
 * Fetches raw color channels or handles fallbacks for currentcolor.
 * @param color - The color input string or pre-parsed channels.
 * @param convertFn - The conversion function to use.
 * @param opt - The conversion options.
 * @returns The color channel values, or null if invalid.
 */
export const getRawChannels = (
  color: string | NumStrColorChannels,
  convertFn: (c: string, opt: any) => any,
  opt: any
): NumStrColorChannels | null => {
  if (Array.isArray(color)) {
    return color as NumStrColorChannels;
  }
  if (/^currentcolor$/i.test(color)) {
    return [NONE, NONE, NONE, NONE];
  }
  return convertFn(color, opt);
};

/**
 * Mixes colors in the sRGB or linear sRGB color space.
 * @param colorSpace - The color space name ('srgb' or 'srgb-linear').
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export const mixSrgbSpace = (
  colorSpace: ColorSpace,
  colorA: string,
  colorB: string,
  pA: number,
  pB: number,
  format: ColorFormat,
  m: number
): SpecifiedColorChannels | null => {
  const convertFn =
    colorSpace === 'srgb' ? convertColorToRgb : convertColorToLinearRgb;
  const rgbA = getRawChannels(colorA, convertFn, {
    colorSpace,
    format: VAL_MIX
  });
  const rgbB = getRawChannels(colorB, convertFn, {
    colorSpace,
    format: VAL_MIX
  });
  if (!rgbA || !rgbB) {
    return null;
  }
  const [rrA, ggA, bbA, aaA] = rgbA;
  const [rrB, ggB, bbB, aaB] = rgbB;
  const nones: [boolean, boolean, boolean, boolean] = [
    rrA === NONE && rrB === NONE,
    ggA === NONE && ggB === NONE,
    bbA === NONE && bbB === NONE,
    aaA === NONE && aaB === NONE
  ];
  const [[rA, gA, bA, alphaA], [rB, gB, bB, alphaB]] = normalizeColorComponents(
    rgbA,
    rgbB,
    true
  );
  const { comps, alpha } = interpolateComponents(
    [rA, gA, bA],
    [rB, gB, bB],
    alphaA,
    alphaB,
    pA,
    pB,
    true
  );
  const rgbOverride: [number, number, number] = [
    comps[0] * MAX_RGB,
    comps[1] * MAX_RGB,
    comps[2] * MAX_RGB
  ];
  return formatMixedColor(
    colorSpace,
    comps,
    nones,
    alpha,
    m,
    format,
    rgbOverride
  );
};

/**
 * Mixes colors in the XYZ, XYZ-D50, or XYZ-D65 color space.
 * @param colorSpace - The color space name.
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export const mixXyzSpace = (
  colorSpace: string,
  colorA: string,
  colorB: string,
  pA: number,
  pB: number,
  format: string,
  m: number
): SpecifiedColorChannels | null => {
  const isD50 = colorSpace === 'xyz-d50';
  const opt = { colorSpace, d50: isD50, format: VAL_MIX };
  const xyzA = getRawChannels(colorA, convertColorToXyz, opt);
  const xyzB = getRawChannels(colorB, convertColorToXyz, opt);
  if (!xyzA || !xyzB) {
    return null;
  }
  const [xxA, yyA, zzA, aaA] = xyzA;
  const [xxB, yyB, zzB, aaB] = xyzB;
  const nones: [boolean, boolean, boolean, boolean] = [
    xxA === NONE && xxB === NONE,
    yyA === NONE && yyB === NONE,
    zzA === NONE && zzB === NONE,
    aaA === NONE && aaB === NONE
  ];
  const [[xA, yA, zA, alphaA], [xB, yB, zB, alphaB]] = normalizeColorComponents(
    xyzA,
    xyzB,
    true
  );
  const { comps, alpha } = interpolateComponents(
    [xA, yA, zA],
    [xB, yB, zB],
    alphaA,
    alphaB,
    pA,
    pB,
    true
  );
  const rgb = isD50
    ? transformXyzD50ToRgb(comps, true)
    : transformXyzToRgb(comps, true);
  return formatMixedColor(colorSpace, comps, nones, alpha, m, format, rgb);
};

/**
 * Mixes colors in the HSL or HWB color space.
 * @param colorSpace - The color space name ('hsl' or 'hwb').
 * @param hueArc - The hue interpolation method.
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export const mixHslHwbSpace = (
  colorSpace: string,
  hueArc: string,
  colorA: string,
  colorB: string,
  pA: number,
  pB: number,
  format: string,
  m: number
): SpecifiedColorChannels | null => {
  const convertFn =
    colorSpace === 'hsl' ? convertColorToHsl : convertColorToHwb;
  const opt = { colorSpace, format: VAL_MIX };
  const hslA = getRawChannels(colorA, convertFn, opt);
  const hslB = getRawChannels(colorB, convertFn, opt);
  if (!hslA || !hslB) {
    return null;
  }
  const [hhA, ssA, llA, aaA] = hslA;
  const [hhB, ssB, llB, aaB] = hslB;
  const nones: [boolean, boolean, boolean, boolean] = [
    hhA === NONE && hhB === NONE,
    ssA === NONE && ssB === NONE,
    llA === NONE && llB === NONE,
    aaA === NONE && aaB === NONE
  ];
  let [[hA, sA, lA, alphaA], [hB, sB, lB, alphaB]] = normalizeColorComponents(
    hslA,
    hslB,
    true
  );
  [hA, hB] = interpolateHue(hA, hB, hueArc);
  const factorA = alphaA * pA;
  const factorB = alphaB * pB;
  const alpha = factorA + factorB;
  const h = (hA * pA + hB * pB) % DEG;
  let s: number, l: number;
  if (alpha === 0) {
    s = sA * pA + sB * pB;
    l = lA * pA + lB * pB;
  } else {
    s = (sA * factorA + sB * factorB) / alpha;
    l = (lA * factorA + lB * factorB) / alpha;
  }
  const [r, g, b] = convertColorToRgb(
    `${colorSpace}(${h} ${s} ${l})`
  ) as ColorChannels;
  const compsForValComp: TriColorChannels = [
    r / MAX_RGB,
    g / MAX_RGB,
    b / MAX_RGB
  ];
  const targetSpace = format === VAL_COMP ? 'srgb' : colorSpace;
  return formatMixedColor(
    targetSpace,
    compsForValComp,
    nones,
    alpha,
    m,
    format,
    [r, g, b]
  );
};

/**
 * Mixes colors in the LCH or Oklch color space.
 * @param colorSpace - The color space name ('lch' or 'oklch').
 * @param hueArc - The hue interpolation method.
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export const mixLchSpace = (
  colorSpace: string,
  hueArc: string,
  colorA: string,
  colorB: string,
  pA: number,
  pB: number,
  format: string,
  m: number
): SpecifiedColorChannels | null => {
  const convertFn =
    colorSpace === 'lch' ? convertColorToLch : convertColorToOklch;
  const opt = { colorSpace, format: VAL_MIX };
  const lchA = getRawChannels(colorA, convertFn, opt);
  const lchB = getRawChannels(colorB, convertFn, opt);
  if (!lchA || !lchB) {
    return null;
  }
  const [llA, ccA, hhA, aaA] = lchA;
  const [llB, ccB, hhB, aaB] = lchB;
  const nones: [boolean, boolean, boolean, boolean] = [
    llA === NONE && llB === NONE,
    ccA === NONE && ccB === NONE,
    hhA === NONE && hhB === NONE,
    aaA === NONE && aaB === NONE
  ];
  let [[lA, cA, hA, alphaA], [lB, cB, hB, alphaB]] = normalizeColorComponents(
    lchA,
    lchB,
    true
  );
  [hA, hB] = interpolateHue(hA, hB, hueArc);
  const factorA = alphaA * pA;
  const factorB = alphaB * pB;
  const alpha = factorA + factorB;
  const h = (hA * pA + hB * pB) % DEG;
  let l: number, c: number;
  if (alpha === 0) {
    l = lA * pA + lB * pB;
    c = cA * pA + cB * pB;
  } else {
    l = (lA * factorA + lB * factorB) / alpha;
    c = (cA * factorA + cB * factorB) / alpha;
  }
  let rgbOverride: [number, number, number] | undefined;
  if (format !== VAL_COMP) {
    const [, r, g, b] = resolveColorValue(
      `${colorSpace}(${l} ${c} ${h})`
    ) as ComputedColorChannels;
    rgbOverride = [r, g, b];
  }
  return formatMixedColor(
    colorSpace,
    [l, c, h],
    nones,
    alpha,
    m,
    format,
    rgbOverride
  );
};

/**
 * Mixes colors in the Lab or Oklab color space.
 * @param colorSpace - The color space name ('lab' or 'oklab').
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export const mixLabSpace = (
  colorSpace: string,
  colorA: string,
  colorB: string,
  pA: number,
  pB: number,
  format: string,
  m: number
): SpecifiedColorChannels | null => {
  const convertFn =
    colorSpace === 'lab' ? convertColorToLab : convertColorToOklab;
  const opt = { colorSpace, format: VAL_MIX };
  const labA = getRawChannels(colorA, convertFn, opt);
  const labB = getRawChannels(colorB, convertFn, opt);
  if (!labA || !labB) {
    return null;
  }
  const [llA, aaA, bbA, alA] = labA;
  const [llB, aaB, bbB, alB] = labB;
  const nones: [boolean, boolean, boolean, boolean] = [
    llA === NONE && llB === NONE,
    aaA === NONE && aaB === NONE,
    bbA === NONE && bbB === NONE,
    alA === NONE && alB === NONE
  ];
  const [[lA, aA, bA, alphaA], [lB, aB, bB, alphaB]] = normalizeColorComponents(
    labA,
    labB,
    true
  );
  const { comps, alpha } = interpolateComponents(
    [lA, aA, bA],
    [lB, aB, bB],
    alphaA,
    alphaB,
    pA,
    pB
  );
  let rgbOverride: [number, number, number] | undefined;
  if (format !== VAL_COMP) {
    const [, r, g, b] = resolveColorValue(
      `${colorSpace}(${comps[0]} ${comps[1]} ${comps[2]})`
    ) as ComputedColorChannels;
    rgbOverride = [r, g, b];
  }
  return formatMixedColor(
    colorSpace,
    comps,
    nones,
    alpha,
    m,
    format,
    rgbOverride
  );
};

/**
 * Computes the mixed color channels based on the specified color space.
 * @param colorSpace - The color space.
 * @param hueArc - The hue interpolation method.
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The normalized ratio for the first color.
 * @param pB - The normalized ratio for the second color.
 * @param m - The multiplier factor.
 * @param format - The output format.
 * @returns The computed color channels, or null if invalid.
 */
export const computeMixedColor = (
  colorSpace: string,
  hueArc: string,
  colorA: string,
  colorB: string,
  pA: number,
  pB: number,
  m: number,
  format: string
): SpecifiedColorChannels | null => {
  let res: SpecifiedColorChannels | null = null;
  if (/^srgb(?:-linear)?$/.test(colorSpace)) {
    res = mixSrgbSpace(colorSpace, colorA, colorB, pA, pB, format, m);
  } else if (/^xyz(?:-d65|-d50)?$/.test(colorSpace)) {
    res = mixXyzSpace(colorSpace, colorA, colorB, pA, pB, format, m);
  } else if (/^h(?:sl|wb)$/.test(colorSpace)) {
    res = mixHslHwbSpace(colorSpace, hueArc, colorA, colorB, pA, pB, format, m);
  } else if (/^(?:ok)?lch$/.test(colorSpace)) {
    res = mixLchSpace(colorSpace, hueArc, colorA, colorB, pA, pB, format, m);
  } else if (/^(?:ok)?lab$/.test(colorSpace)) {
    res = mixLabSpace(colorSpace, colorA, colorB, pA, pB, format, m);
  }
  return res;
};

/**
 * Determines whether the given value is possibly a percentage.
 * @param value - value
 * @returns True if the value is percentage, false otherwise.
 */
export const isPossiblyPercentage = (value: string): boolean => {
  if (!isString(value)) {
    return false;
  }
  const str = value.toLowerCase().trim();
  return str.endsWith('%') || str.startsWith(FN_CALC) || str.startsWith(FN_VAR);
};

/**
 * Parses a color and percentage argument string.
 * @param arg - The color and percentage argument string to parse.
 * @returns An object containing the color string and percentage string.
 */
export const parseColorAndPct = (arg: string): ColorAndPct => {
  const tokens = splitValue(arg).filter(Boolean);
  let color = '';
  let pct = '';
  if (tokens.length === 1) {
    color = tokens[0] as string;
  } else if (isPossiblyPercentage(tokens.at(-1) as string)) {
    pct = tokens.pop() as string;
    color = tokens.join(' ');
  } else if (isPossiblyPercentage(tokens[0] as string)) {
    pct = tokens.shift() as string;
    color = tokens.join(' ');
  } else {
    color = tokens.join(' ');
  }
  return { color, pct };
};

/**
 * Parses inner content color items
 * @param items - The color items
 * @returns The parsed color-mix() inner colors and total percentage.
 */
export const parseInnerContentColorItems = (
  items: string[]
): ColorMixInnerColorItem | null => {
  const colors = [];
  let totalPercentage = 0;
  let pended = false;
  for (const item of items) {
    const parsedItem = parseColorAndPct(item as string);
    const { color, pct } = parsedItem;
    if (!color) {
      return null;
    }
    const loweredColor = color.toLowerCase();
    if (loweredColor.startsWith(FN_VAR)) {
      pended = true;
      totalPercentage = Number.NaN;
    }
    const loweredPct = pct.toLowerCase();
    if (loweredPct.startsWith(FN_CALC) || loweredPct.startsWith(FN_VAR)) {
      pended = true;
      totalPercentage = Number.NaN;
    } else if (pct.endsWith('%')) {
      const value = parseFloat(pct);
      if (value < 0) {
        return null;
      }
      if (!pended) {
        totalPercentage += value;
      }
    }
    colors.push(parsedItem);
  }
  return {
    colors,
    totalPercentage
  };
};

/**
 * Parses a color space argument string.
 * @param arg - The color space argument string to parse.
 * @returns An object containing the color space and hue arc, or null if invalid.
 */
export const parseColorSpace = (arg: string): ColorSpaceParseResult | null => {
  const csTokens = splitValue(arg).filter(Boolean);
  if (csTokens[0] !== 'in' || csTokens.length < 2) {
    return null;
  }
  const hueIndex = csTokens.indexOf('hue');
  if (csTokens.length > 2 && hueIndex > -1) {
    return {
      colorSpace: csTokens[1] as string,
      hueArc: csTokens.slice(2, hueIndex).join(' ')
    };
  }
  return {
    colorSpace: csTokens.slice(1).join(' '),
    hueArc: ''
  };
};

/**
 * Parses inner content of the color-mix().
 * @param value - The color-mix() value.
 * @returns The parsed color-mix() inner content.
 */
export const parseColorMixInnerContent = (
  value: string
): ColorMixInnerContent | null => {
  if (!isString(value)) {
    return null;
  }
  value = value.trim();
  const str = value.toLowerCase();
  if (!str.startsWith(FN_MIX) || !str.endsWith(')')) {
    return null;
  }
  const innerContent = value.slice(10, -1).trim();
  if (!innerContent) {
    return null;
  }
  const [firstItem, ...restItems] = splitValue(innerContent, {
    delimiter: ','
  }).map(s => s.trim());
  const colors = [];
  let totalPercentage = 0;
  const parsedColorSpace = parseColorSpace(firstItem as string);
  if (parsedColorSpace) {
    const { colorSpace, hueArc } = parsedColorSpace;
    const parsedColorItems = parseInnerContentColorItems(restItems);
    if (!parsedColorItems) {
      return null;
    }
    colors.push(...parsedColorItems.colors);
    totalPercentage += parsedColorItems.totalPercentage;
    if (!colors.length) {
      return null;
    }
    return {
      colors,
      colorSpace,
      hueArc,
      totalPercentage
    };
  }
  const parsedFirstItem = parseInnerContentColorItems([firstItem as string]);
  if (!parsedFirstItem) {
    return null;
  }
  colors.push(...parsedFirstItem.colors);
  totalPercentage += parsedFirstItem.totalPercentage;
  const parsedRestItems = parseInnerContentColorItems(restItems);
  if (!parsedRestItems) {
    return null;
  }
  colors.push(...parsedRestItems.colors);
  totalPercentage += parsedRestItems.totalPercentage;
  return {
    colors,
    colorSpace: null,
    hueArc: '',
    totalPercentage
  };
};

/**
 * Resolves nested functions recursively.
 * @param colorStr - The color string to resolve.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color string, or the original string if no nesting is present.
 */
export const resolveIfNested = (
  colorStr: string,
  opt: Options,
  resolver: ColorResolverFn
): string => {
  let current = colorStr;
  let loweredStr = current.toLowerCase();
  while (
    isString(loweredStr) &&
    (loweredStr.startsWith(FN_MIX) ||
      loweredStr.startsWith(FN_VAR) ||
      loweredStr.startsWith(FN_LIGHT_DARK))
  ) {
    const resolved = resolver(current, {
      ...opt,
      format: opt.format === VAL_SPEC ? opt.format : VAL_COMP
    });
    if (!resolved) {
      break;
    }
    if (Array.isArray(resolved)) {
      const [cs, v1, v2, v3, v4] = resolved;
      const alphaVal = v4 === 1 || v4 === undefined ? '' : ` / ${v4}`;
      const alphaRgba = v4 === 1 || v4 === undefined ? '' : `, ${v4}`;
      if (/^rgb$/.test(cs)) {
        current =
          v4 === 1 || v4 === undefined
            ? `rgb(${v1}, ${v2}, ${v3})`
            : `rgba(${v1}, ${v2}, ${v3}${alphaRgba})`;
      } else if (/^(?:hsl|hwb|lab|lch|oklab|oklch)$/.test(cs)) {
        current = `${cs}(${v1} ${v2} ${v3}${alphaVal})`;
      } else {
        current = `color(${cs} ${v1} ${v2} ${v3}${alphaVal})`;
      }
      break;
    }
    if (typeof resolved === 'string') {
      if (resolved === current) {
        break;
      }
      current = resolved;
      loweredStr = current.toLowerCase();
    } else {
      break;
    }
  }
  return current;
};

/**
 * Normalizes percentages for multiple colors in color-mix() according to CSS Color Module Level 5.
 * @param colors - Array of parsed color and percentage items.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns An object containing normalized colors with ratios (0-1) and a multiplier m, or null if invalid.
 */
export const normalizeMixPercentages = (
  colors: ColorAndPct[],
  opt: Options,
  resolver: ColorResolverFn
): ColorsAndMultipler | null => {
  const len = colors.length;
  if (len === 0) {
    return null;
  }
  const pcts: (number | null)[] = [];
  let specifiedSum = 0;
  let emptyCount = 0;
  for (let i = 0; i < len; i++) {
    const item = colors[i] as ColorAndPct;
    item.color = resolveIfNested(item.color, opt, resolver);
    const pctStr = item.pct.trim();
    if (pctStr === '') {
      pcts.push(null);
      emptyCount++;
    } else {
      const val = parseFloat(pctStr);
      if (Number.isNaN(val) || val < 0 || val > MAX_PCT) {
        return null;
      }
      pcts.push(val);
      specifiedSum += val;
    }
  }
  const resolvedPcts: number[] = new Array(len);
  let m = 1;
  if (emptyCount === len) {
    const equalVal = MAX_PCT / len;
    for (let i = 0; i < len; i++) {
      resolvedPcts[i] = equalVal;
    }
    specifiedSum = MAX_PCT;
  } else if (emptyCount > 0) {
    const remaining = MAX_PCT - specifiedSum;
    const defaultVal = remaining > 0 ? remaining / emptyCount : 0;
    for (let i = 0; i < len; i++) {
      if (pcts[i] === null) {
        resolvedPcts[i] = defaultVal;
      } else {
        resolvedPcts[i] = pcts[i] as number;
      }
    }
    specifiedSum = Math.max(MAX_PCT, specifiedSum);
  } else {
    for (let i = 0; i < len; i++) {
      resolvedPcts[i] = pcts[i] as number;
    }
  }
  if (specifiedSum === 0) {
    const equalVal = MAX_PCT / len;
    for (let i = 0; i < len; i++) {
      resolvedPcts[i] = equalVal;
    }
    m = 0;
  } else {
    const totalFactor = specifiedSum / MAX_PCT;
    if (specifiedSum > MAX_PCT) {
      for (let i = 0; i < len; i++) {
        resolvedPcts[i] = (resolvedPcts[i] as number) / totalFactor;
      }
      m = 1;
    } else if (specifiedSum < MAX_PCT) {
      for (let i = 0; i < len; i++) {
        resolvedPcts[i] = (resolvedPcts[i] as number) / totalFactor;
      }
      m = totalFactor;
    }
  }
  const resultColors = colors.map((item, i) => ({
    color: item.color,
    ratio: (resolvedPcts[i] as number) / MAX_PCT
  }));
  return {
    colors: resultColors,
    m
  };
};

/**
 * Normalizes color values and percentage values.
 * @param colors - The list of color items.
 * @param totalPercentage - The total percentage.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The normalized color-mix() inner colors and total percentage.
 */
export const normalizeColorItems = (
  colors: ColorAndPct[],
  totalPercentage: number,
  opt: Options,
  resolver: ColorResolverFn
): ColorMixInnerColorItem => {
  const colorsLength = colors.length;
  const emptyPcts = [];
  for (let i = 0; i < colorsLength; i++) {
    const item = colors[i] as ColorAndPct;
    const { color, pct } = item;
    item.color = resolveIfNested(color, opt, resolver);
    if (pct === '') {
      if (totalPercentage > 100) {
        item.pct = '0%';
      } else if (totalPercentage > 0) {
        emptyPcts.push(i);
      }
    }
  }
  if (emptyPcts.length) {
    const propotionalPct = roundToPrecision(
      (MAX_PCT - totalPercentage) / emptyPcts.length,
      HEX
    );
    for (const index of emptyPcts) {
      const item = colors[index] as ColorAndPct;
      item.pct = `${propotionalPct}%`;
    }
    totalPercentage += propotionalPct * emptyPcts.length;
  }
  return {
    colors,
    totalPercentage
  };
};

/**
 * Processes and standardizes a color value string.
 * @param colorStr - The color string to process.
 * @param opt - The conversion options.
 * @returns The processed specified color string.
 */
export const processColor = (colorStr: string, opt: Options): string => {
  if (!isString(colorStr)) {
    return '';
  }
  const loweredStr = colorStr.toLowerCase();
  if (loweredStr.startsWith(FN_MIX) || loweredStr.startsWith(FN_LIGHT_DARK)) {
    return colorStr;
  }
  if (loweredStr.startsWith(FN_COLOR)) {
    const [cs, v1, v2, v3, v4] = parseColorFunc(
      colorStr,
      opt
    ) as SpecifiedColorChannels;
    if (v4 === 1) {
      return `color(${cs} ${v1} ${v2} ${v3})`;
    } else {
      return `color(${cs} ${v1} ${v2} ${v3} / ${v4})`;
    }
  }
  const val = parseColorValue(colorStr, opt);
  if (Array.isArray(val)) {
    const [cs, v1, v2, v3, v4] = val;
    if (v4 === 1) {
      if (cs === 'rgb') {
        return `${cs}(${v1}, ${v2}, ${v3})`;
      }
      return `${cs}(${v1} ${v2} ${v3})`;
    } else {
      if (cs === 'rgb') {
        return `${cs}a(${v1}, ${v2}, ${v3}, ${v4})`;
      }
      return `${cs}(${v1} ${v2} ${v3} / ${v4})`;
    }
  }
  if (!isString(val) || !val) {
    return '';
  }
  return val as string;
};

/**
 * Formats a specified value for the color-mix().
 * @param colorSpace - The target color space.
 * @param hueArc - The hue interpolation method (hue arc).
 * @param colors - The list of color items.
 * @param opt - The conversion options.
 * @returns The specified value for the color-mix().
 */
export const formatSpecifiedValue = (
  colorSpace: string | null,
  hueArc: string,
  colors: ColorAndPct[],
  opt: Options
): string => {
  const items = [];
  for (const { color, pct } of colors) {
    const colorValue = processColor(color, opt);
    if (!colorValue) {
      return '';
    }
    items.push(`${colorValue} ${pct}`.trim());
  }
  const colorItems = items.join(', ');
  if (colorSpace) {
    if (hueArc) {
      return `color-mix(in ${colorSpace} ${hueArc} hue, ${colorItems})`;
    }
    return `color-mix(in ${colorSpace}, ${colorItems})`;
  }
  return `color-mix(${colorItems})`;
};

/**
 * Resolves a CSS color-mix() function value.
 * @param value - The color-mix() value to resolve.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color channels, formatted string, or null.
 */
export const resolveColorMix = (
  value: string,
  opt: Options = {},
  resolver: ColorResolverFn = () => null
): SpecifiedColorChannels | string | null => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  value = value.trim();
  const cacheKey: string = createCacheKey(
    { namespace: NAMESPACE, name: 'resolveColorMix', value },
    opt
  );
  const cachedResult = getCache(cacheKey);
  if (cachedResult !== false) {
    const { item } = cachedResult;
    if (Array.isArray(item)) {
      return [...item] as SpecifiedColorChannels;
    }
    if (isString(item)) {
      return item;
    }
    return null;
  }
  const { format = '', nullable = false } = opt;
  const str = cssCalc(value, opt);
  const parsedInnerContent = parseColorMixInnerContent(str);
  if (!parsedInnerContent) {
    return cacheInvalidColorValue(cacheKey, format, nullable);
  }
  let { colorSpace, colors, hueArc, totalPercentage } = parsedInnerContent;
  if (!colorSpace && format !== VAL_SPEC) {
    colorSpace = 'oklab';
  } else if (colorSpace === 'xyz') {
    colorSpace = 'xyz-d65';
  }
  const normalizedColorItems = normalizeColorItems(
    colors,
    totalPercentage,
    opt,
    resolver
  );
  colors = normalizedColorItems.colors;
  totalPercentage = normalizedColorItems.totalPercentage;
  if (format === VAL_SPEC) {
    const specifiedValue = formatSpecifiedValue(
      colorSpace,
      hueArc,
      colors,
      opt
    );
    if (!specifiedValue) {
      return cacheInvalidColorValue(cacheKey, format, nullable);
    }
    setCache(cacheKey, specifiedValue);
    return specifiedValue;
  }
  const normalized = normalizeMixPercentages(colors, opt, resolver);
  if (!normalized) {
    return cacheInvalidColorValue(cacheKey, format, nullable);
  }
  const { colors: normalizedColors, m } = normalized;
  const len = normalizedColors.length;
  const firstColorItem = normalizedColors[0] as ColorAndRatio;
  let currentColor = firstColorItem.color;
  let currentRatio = firstColorItem.ratio;
  let computedRes: SpecifiedColorChannels | null = null;
  if (len === 1) {
    computedRes = computeMixedColor(
      colorSpace as string,
      hueArc,
      currentColor,
      currentColor,
      1,
      0,
      m,
      format
    );
  } else {
    for (let i = 1; i < len; i++) {
      const nextColorItem = normalizedColors[i] as ColorAndRatio;
      const nextColor = nextColorItem.color;
      const nextRatio = nextColorItem.ratio;
      const sumRatio = currentRatio + nextRatio;
      let pA = 0.5;
      let pB = 0.5;
      if (sumRatio > 0) {
        pA = currentRatio / sumRatio;
        pB = nextRatio / sumRatio;
      }
      computedRes = computeMixedColor(
        colorSpace as string,
        hueArc,
        currentColor,
        nextColor,
        pA,
        pB,
        i === len - 1 ? m : 1,
        format
      );
      if (!Array.isArray(computedRes)) {
        return cacheInvalidColorValue(cacheKey, format, nullable);
      }
      const [cs, c1, c2, c3, ca] = computedRes;
      const alphaVal = ca === 1 || ca === undefined ? '' : ` / ${ca}`;
      const alphaRgba = ca === 1 || ca === undefined ? '' : `, ${ca}`;
      if (/^rgb$/.test(cs)) {
        currentColor =
          ca === 1 || ca === undefined
            ? `rgb(${c1}, ${c2}, ${c3})`
            : `rgba(${c1}, ${c2}, ${c3}${alphaRgba})`;
      } else if (/^(?:hsl|hwb|lab|lch|oklab|oklch)$/.test(cs)) {
        currentColor = `${cs}(${c1} ${c2} ${c3}${alphaVal})`;
      } else {
        currentColor = `color(${cs} ${c1} ${c2} ${c3}${alphaVal})`;
      }
      currentRatio = sumRatio;
    }
  }
  if (!computedRes) {
    return cacheInvalidColorValue(cacheKey, format, nullable);
  }
  setCache(cacheKey, computedRes);
  return computedRes;
};
